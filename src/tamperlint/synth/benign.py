"""Legitimate operations that change a PDF without tampering with its content.

The evaluation uses these to measure false positives: a good detector stays quiet on them.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pikepdf
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter


def linearize(data: bytes) -> bytes:
    """ "Fast web view" optimisation, as done by many document portals."""
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        pdf.save(out, linearize=True)
    return out.getvalue()


def full_resave(data: bytes) -> bytes:
    """Rewrite the whole file (e.g. a compressor or "save as" in a viewer)."""
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        pdf.save(out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return out.getvalue()


def add_sticky_note(data: bytes, text: str = "Received, thanks.") -> bytes:
    """Append a reviewer's sticky note in the page margin as an incremental update."""
    writer = IncrementalPdfFileWriter(io.BytesIO(data))
    page_ref, _ = writer.find_page_for_modification(0)
    page = page_ref.get_object()
    annot = generic.DictionaryObject(
        {
            generic.NameObject("/Type"): generic.NameObject("/Annot"),
            generic.NameObject("/Subtype"): generic.NameObject("/Text"),
            generic.NameObject("/Rect"): generic.ArrayObject(
                [generic.FloatObject(v) for v in (560, 780, 580, 800)]
            ),
            generic.NameObject("/Contents"): generic.TextStringObject(text),
        }
    )
    annot_ref = writer.add_object(annot)
    annots = page.get("/Annots")
    new_annots = generic.ArrayObject(list(annots) if annots is not None else [])
    new_annots.append(annot_ref)
    page[generic.NameObject("/Annots")] = new_annots
    writer.update_container(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _self_signed(signer: str = "Demo Bank Ltd (SPECIMEN)") -> tuple[object, object]:
    from asn1crypto import keys, x509
    from cryptography import x509 as cx509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, signer)])
    now = datetime.now(UTC)
    cert = (
        cx509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(cx509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(cx509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            cx509.KeyUsage(True, True, False, False, False, False, False, False, False),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    asn_cert = x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    asn_key = keys.PrivateKeyInfo.load(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return asn_cert, asn_key


def sign(
    data: bytes, field_name: str = "IssuerSignature", signer: str = "Demo Bank Ltd (SPECIMEN)"
) -> bytes:
    """Digitally sign the document with a throw-away self-signed certificate for ``signer``."""
    from pyhanko.sign import signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    cert, key = _self_signed(signer)
    pdf_signer = signers.SimpleSigner(
        signing_cert=cert, signing_key=key, cert_registry=SimpleCertificateStore()
    )
    writer = IncrementalPdfFileWriter(io.BytesIO(data))
    out = signers.sign_pdf(
        writer, signers.PdfSignatureMetadata(field_name=field_name), signer=pdf_signer
    )
    return bytes(out.getvalue())
