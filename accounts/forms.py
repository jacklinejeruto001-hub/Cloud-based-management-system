from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import Profile

User = get_user_model()


def _style(form):
    """Apply Bootstrap classes to all widgets."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = "form-check-input"
        elif isinstance(widget, forms.Select):
            widget.attrs["class"] = "form-select"
        else:
            widget.attrs["class"] = "form-control"


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        self.fields["username"].widget.attrs.update({"autofocus": True, "placeholder": "Username"})
        self.fields["password"].widget.attrs.update({"placeholder": "Password"})


class UserCreateForm(UserCreationForm):
    role = forms.ChoiceField(choices=Profile.Role.choices, initial=Profile.Role.STAFF)
    phone = forms.CharField(max_length=30, required=False)
    company = forms.CharField(max_length=120, required=False, help_text="For client accounts.")

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            profile = user.profile
            profile.role = self.cleaned_data["role"]
            profile.phone = self.cleaned_data["phone"]
            profile.company = self.cleaned_data["company"]
            profile.save()
            user.is_staff = profile.role == Profile.Role.ADMIN
            user.save(update_fields=["is_staff"])
        return user


class UserUpdateForm(forms.ModelForm):
    role = forms.ChoiceField(choices=Profile.Role.choices)
    phone = forms.CharField(max_length=30, required=False)
    company = forms.CharField(max_length=120, required=False)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        profile = self.instance.profile
        self.fields["role"].initial = profile.role
        self.fields["phone"].initial = profile.phone
        self.fields["company"].initial = profile.company

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            profile = user.profile
            profile.role = self.cleaned_data["role"]
            profile.phone = self.cleaned_data["phone"]
            profile.company = self.cleaned_data["company"]
            profile.save()
            if not user.is_superuser:
                user.is_staff = profile.role == Profile.Role.ADMIN
                user.save(update_fields=["is_staff"])
        return user
