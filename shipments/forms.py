import base64
import binascii
import os
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.models import Profile

from .models import DocType, Shipment, Status

User = get_user_model()


def style_form(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.HiddenInput):
            continue
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = "form-check-input"
        elif isinstance(widget, forms.Select):
            widget.attrs["class"] = "form-select"
        elif isinstance(widget, forms.ClearableFileInput):
            widget.attrs["class"] = "form-control"
        else:
            widget.attrs["class"] = "form-control"


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        name = obj.get_full_name() or obj.get_username()
        company = getattr(getattr(obj, "profile", None), "company", "")
        return f"{name} ({company})" if company else name


class ShipmentForm(forms.ModelForm):
    # Optimistic-locking token: the version the user was looking at when editing.
    version = forms.IntegerField(required=False, widget=forms.HiddenInput)
    client = UserChoiceField(queryset=User.objects.none(), required=False, label="Client account")
    assigned_to = UserChoiceField(queryset=User.objects.none(), required=False, label="Assigned dispatcher / driver")

    class Meta:
        model = Shipment
        fields = [
            "reference_number", "sender_name", "sender_phone", "receiver_name", "receiver_phone",
            "receiver_email", "origin", "destination", "description", "weight_kg", "packages",
            "expected_delivery", "client", "assigned_to",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "expected_delivery": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = User.objects.filter(
            is_active=True, profile__role=Profile.Role.CLIENT
        ).order_by("first_name", "username")
        self.fields["assigned_to"].queryset = User.objects.filter(
            is_active=True, profile__role__in=[Profile.Role.DISPATCH, Profile.Role.STAFF]
        ).order_by("first_name", "username")
        style_form(self)

    def clean_weight_kg(self):
        weight = self.cleaned_data["weight_kg"]
        if weight < Decimal("0"):
            raise forms.ValidationError("Weight cannot be negative.")
        return weight

    def clean_packages(self):
        packages = self.cleaned_data["packages"]
        if packages < 1:
            raise forms.ValidationError("A shipment must have at least one package.")
        return packages

    def clean_receiver_email(self):
        return self.cleaned_data["receiver_email"].strip().lower()

    def clean(self):
        cleaned = super().clean()
        for name in ("sender_name", "receiver_name", "origin", "destination", "reference_number"):
            if isinstance(cleaned.get(name), str):
                cleaned[name] = " ".join(cleaned[name].split())
        return cleaned


class ShipmentEventForm(forms.Form):
    """Status update. 'Delivered' is deliberately absent: it can only be set by
    capturing proof of delivery, so no delivery is ever left unconfirmed."""

    status = forms.ChoiceField(choices=[])
    location = forms.CharField(max_length=160, required=False)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, can_cancel=False, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = [Status.PICKED_UP, Status.IN_TRANSIT, Status.OUT_FOR_DELIVERY, Status.FAILED]
        if can_cancel:
            allowed.append(Status.CANCELLED)
        self.fields["status"].choices = [(s.value, s.label) for s in allowed]
        style_form(self)


class DocumentUploadForm(forms.Form):
    doc_type = forms.ChoiceField(choices=DocType.choices, label="Document type")
    title = forms.CharField(max_length=160, required=False, help_text="Required for type 'Other'.")
    file = forms.FileField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_form(self)

    def clean_file(self):
        upload = self.cleaned_data["file"]
        ext = os.path.splitext(upload.name)[1].lower().lstrip(".")
        if ext not in settings.CBSRMS["ALLOWED_UPLOAD_EXTENSIONS"]:
            raise forms.ValidationError(
                "File type not allowed. Allowed: " + ", ".join(settings.CBSRMS["ALLOWED_UPLOAD_EXTENSIONS"]) + "."
            )
        if upload.size > settings.CBSRMS["MAX_UPLOAD_MB"] * 1024 * 1024:
            raise forms.ValidationError(f"File is larger than {settings.CBSRMS['MAX_UPLOAD_MB']} MB.")
        if upload.size == 0:
            raise forms.ValidationError("The file is empty.")
        return upload

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("doc_type") == DocType.OTHER and not cleaned.get("title"):
            self.add_error("title", "Please give the document a title.")
        return cleaned


class DeliveryConfirmationForm(forms.Form):
    received_by = forms.CharField(max_length=120, label="Received by (full name)")
    receiver_id_number = forms.CharField(max_length=40, required=False, label="ID / staff number")
    relationship = forms.CharField(max_length=60, required=False, label="Relationship to receiver")
    delivered_at = forms.DateTimeField(
        label="Delivered at",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    photo = forms.ImageField(required=False, label="Photo of delivered goods (optional if signed)")
    signature_data = forms.CharField(required=False, widget=forms.HiddenInput)
    latitude = forms.DecimalField(required=False, max_digits=9, decimal_places=6, widget=forms.HiddenInput)
    longitude = forms.DecimalField(required=False, max_digits=9, decimal_places=6, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["delivered_at"].initial = timezone.localtime().replace(second=0, microsecond=0)
        style_form(self)

    def clean_delivered_at(self):
        value = self.cleaned_data["delivered_at"]
        if value > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError("Delivery time cannot be in the future.")
        return value

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and photo.size > settings.CBSRMS["MAX_UPLOAD_MB"] * 1024 * 1024:
            raise forms.ValidationError("Photo is too large.")
        return photo

    def clean_signature_data(self):
        raw = (self.cleaned_data.get("signature_data") or "").strip()
        if not raw:
            return None
        if "," not in raw or not raw.startswith("data:image/png;base64"):
            raise forms.ValidationError("Invalid signature data.")
        try:
            blob = base64.b64decode(raw.split(",", 1)[1], validate=True)
        except (binascii.Error, ValueError):
            raise forms.ValidationError("Invalid signature data.")
        if not blob.startswith(b"\x89PNG") or len(blob) > 1_500_000:
            raise forms.ValidationError("Invalid signature image.")
        return ContentFile(blob, name="signature.png")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("signature_data") and not cleaned.get("photo") and not self.errors:
            raise forms.ValidationError("Proof of delivery needs a signature or a photo.")
        return cleaned
