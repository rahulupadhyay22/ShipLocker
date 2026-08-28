import logging

logger = logging.getLogger('security')


class DeclarationService:
    """Single entry point for customs declaration PDF generation. Mirrors
    apps.payments.services.InvoiceService — same reportlab + upload pattern."""

    @staticmethod
    def validate_signature_fields(post_data):
        """Validate the e-sign fields from a shipment-creation POST.

        Returns (declaration_purpose, signature_name) on success. Raises
        django.core.exceptions.ValidationError with a user-facing message on
        the first invalid field. signature_name is capped at 255 chars (the
        Shipment.declaration_signed_name column width) — this is shape
        validation only, not identity/KYC validation, which the spec
        deliberately excludes.
        """
        from django.core.exceptions import ValidationError
        from apps.shipments.models import Shipment

        declaration_purpose = post_data.get('declaration_purpose', '')
        if declaration_purpose not in dict(Shipment.DECLARATION_PURPOSE_CHOICES):
            raise ValidationError('Please select a declaration purpose.')

        if post_data.get('signature_agree') != 'on':
            raise ValidationError('Please agree to the Customer Declaration & Authorization to continue.')

        signature_name = post_data.get('signature_name', '').strip()[:255]
        if not signature_name:
            raise ValidationError('Please type your full name to sign the declaration.')

        return declaration_purpose, signature_name

    @staticmethod
    def generate_pdf(shipment, parcels):
        import io
        from xml.sax.saxutils import escape
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph('CamelTrunk Customs Declaration & Authorization', styles['Title']))
        story.append(Paragraph(f"Shipment: {shipment.display_id}", styles['Normal']))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph('Ship To:', styles['Heading3']))
        story.append(Paragraph(shipment.recipient_name, styles['Normal']))
        address_lines = [shipment.address_line1]
        if shipment.address_line2:
            address_lines.append(shipment.address_line2)
        address_lines.append(f"{shipment.city}, {shipment.state} {shipment.postal_code}")
        address_lines.append(shipment.country)
        story.append(Paragraph('<br/>'.join(address_lines), styles['Normal']))
        story.append(Spacer(1, 8 * mm))

        story.append(Paragraph('Shipment Contents', styles['Heading3']))
        rows = [['Item', 'Category', 'Description', 'Value', 'Weight (kg)']]
        total_value = 0
        currency = ''
        for parcel in parcels:
            currency = parcel.item_currency or currency
            value = parcel.item_price or 0
            total_value += value
            rows.append([
                parcel.item_name or parcel.tracking_number or '-',
                parcel.get_category_display() if parcel.category else '-',
                parcel.customs_description or '-',
                f"{parcel.item_currency} {value}",
                str(parcel.weight_kg or '-'),
            ])
        rows.append(['Total', '', '', f"{currency} {total_value}".strip(), ''])

        table = Table(rows, colWidths=[35 * mm, 25 * mm, 55 * mm, 30 * mm, 25 * mm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003746')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
        ]))
        story.append(table)
        story.append(Spacer(1, 6 * mm))

        purpose_display = dict(shipment.DECLARATION_PURPOSE_CHOICES).get(shipment.declaration_purpose, '-')
        story.append(Paragraph(f"Declaration Purpose: {purpose_display}", styles['Normal']))
        story.append(Spacer(1, 8 * mm))

        story.append(Paragraph('Customer Declaration & Authorization', styles['Heading3']))
        for line in shipment.DECLARATION_TEXT.split('\n\n'):
            story.append(Paragraph(line.replace('\n', '<br/>'), styles['Normal']))
            story.append(Spacer(1, 3 * mm))

        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph('Electronic Signature', styles['Heading3']))
        # signature_name is free text a customer typed (no KYC/identity
        # validation, by design — see spec) so it must be escaped before
        # going into a reportlab Paragraph, which — unlike the plain-text
        # Table cells above — interprets a subset of HTML-like markup.
        story.append(Paragraph(f"Signed by: {escape(shipment.declaration_signed_name)}", styles['Normal']))
        story.append(Paragraph(
            f"Signed at: {shipment.declaration_signed_at.strftime('%d %b %Y, %H:%M %Z') if shipment.declaration_signed_at else '-'}",
            styles['Normal'],
        ))
        story.append(Paragraph(
            f"Declaration version: {shipment.declaration_version} — Signed electronically via CamelTrunk",
            styles['Normal'],
        ))

        doc.build(story)
        return buffer.getvalue()

    @staticmethod
    def upload_pdf(pdf_bytes, shipment):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.locker.utils import upload_shipment_document, get_user_locker_id

        locker_id = get_user_locker_id(shipment.user)
        filename = f"declaration_{shipment.display_id}.pdf"
        uploaded_file = SimpleUploadedFile(filename, pdf_bytes, content_type='application/pdf')
        return upload_shipment_document(uploaded_file, locker_id, shipment.display_id, 'customs')
