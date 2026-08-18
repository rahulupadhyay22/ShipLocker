"""ShipmentDocument rows must not orphan their Supabase Storage file on delete."""

from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User
from apps.shipments.models import Shipment, ShipmentDocument


def make_shipment(user, **extra):
    defaults = dict(
        user=user,
        shipment_type='international',
        recipient_name='Jane Doe',
        recipient_phone='9999999999',
        address_line1='1 Test St',
        city='Testville',
        state='TS',
        postal_code='000000',
        country='USA',
    )
    defaults.update(extra)
    return Shipment.objects.create(**defaults)


class ShipmentDocumentStorageCleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='shipdoc@example.com')
        self.shipment = make_shipment(self.user)

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_deleting_document_deletes_its_storage_file(self, mock_delete):
        doc = ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='invoice',
            document_url='shipment/RB-1/invoice_RB-1-S001_abc123.pdf',
        )

        doc.delete()

        mock_delete.assert_called_once_with('invoices', 'shipment/RB-1/invoice_RB-1-S001_abc123.pdf')

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_deleting_shipment_cascades_to_its_documents_storage_files(self, mock_delete):
        ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='invoice', document_url='shipment/RB-1/invoice_x.pdf',
        )
        ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='customs', document_url='shipment/RB-1/customs_y.pdf',
        )

        self.shipment.delete()

        self.assertEqual(mock_delete.call_count, 2)

    @patch('apps.accounts.services.SupabaseStorage.delete_file', side_effect=Exception('storage down'))
    def test_storage_failure_does_not_block_the_db_delete(self, mock_delete):
        doc = ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='label', document_url='shipment/RB-1/label_z.pdf',
        )
        doc_pk = doc.pk

        doc.delete()  # must not raise

        self.assertFalse(ShipmentDocument.objects.filter(pk=doc_pk).exists())

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_deleting_document_still_referenced_by_invoice_keeps_the_file(self, mock_delete):
        from apps.payments.models import Invoice

        path = 'shipment/RB-1/invoice_RB-1-S001_abc123.pdf'
        doc = ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='invoice', document_url=path,
        )
        Invoice.objects.create(
            shipment=self.shipment,
            invoice_number='INV/2026-27/0001',
            invoice_date=self.shipment.created_at,
            customer_name='Jane Doe',
            billing_address='1 Test St',
            amount_paid=100, taxable_amount=100, total_amount=100,
            pdf_document_url=path,
        )

        doc.delete()

        mock_delete.assert_not_called()
