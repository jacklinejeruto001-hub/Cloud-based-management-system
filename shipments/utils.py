"""Small helpers with no model imports (safe to import from models.py)."""
import hashlib
import re
from decimal import Decimal, InvalidOperation


def normalize(value):
    """Lower-case, trim and collapse whitespace so trivial differences don't matter."""
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def compute_fingerprint(sender_name, receiver_name, origin, destination, description, weight_kg, packages):
    """
    SHA-256 fingerprint of the business identity of a shipment. Two records
    with the same fingerprint describe the same consignment, i.e. a duplicate.
    """
    try:
        weight = f"{Decimal(str(weight_kg or 0)):.2f}"
    except InvalidOperation:
        weight = "0.00"
    parts = [
        normalize(sender_name),
        normalize(receiver_name),
        normalize(origin),
        normalize(destination),
        normalize(description),
        weight,
        str(packages or 1),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def sha256_of_file(fileobj):
    """Hash an uploaded file in chunks, then rewind it so it can still be saved."""
    digest = hashlib.sha256()
    fileobj.seek(0)
    for chunk in iter(lambda: fileobj.read(64 * 1024), b""):
        digest.update(chunk)
    fileobj.seek(0)
    return digest.hexdigest()
