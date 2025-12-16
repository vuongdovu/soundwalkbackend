"""
Tests for malware scanning Celery tasks.

These tests verify:
- scan_file_for_malware task behavior
- Chain continuation for clean files
- Chain rejection for infected files
- Fail-open behavior for scanner outages
- rescan_skipped_files periodic task
- check_antivirus_health monitoring task
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Reject
from django.core.cache import cache

from media.models import MediaFile


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test."""
    cache.clear()
    yield
    cache.clear()


class TestScanFileForMalwareTask:
    """Test the scan_file_for_malware Celery task."""

    def test_clean_file_returns_continue_dict(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Clean file should return dict to continue chain."""
        from media.services.scanner import MalwareScanResult
        from media.tasks import scan_file_for_malware

        clean_result = MalwareScanResult.clean(scan_method="stream")

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.scan_media_file.return_value = clean_result

            result = scan_file_for_malware(str(media_file_pending_scan.id))

            assert result["status"] == "clean"
            assert result["media_file_id"] == str(media_file_pending_scan.id)

    def test_clean_file_updates_status(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Clean file should update scan_status to CLEAN."""
        from media.services.scanner import MalwareScanResult
        from media.tasks import scan_file_for_malware

        clean_result = MalwareScanResult.clean(scan_method="stream")

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.scan_media_file.return_value = clean_result

            scan_file_for_malware(str(media_file_pending_scan.id))

            media_file_pending_scan.refresh_from_db()
            assert media_file_pending_scan.scan_status == MediaFile.ScanStatus.CLEAN
            assert media_file_pending_scan.scanned_at is not None

    def test_infected_file_raises_reject(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Infected file should raise Reject to stop chain."""
        from media.services.scanner import MalwareScanResult
        from media.tasks import scan_file_for_malware

        infected_result = MalwareScanResult.infected(
            threat_name="Eicar-Signature",
            scan_method="stream",
        )

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.scan_media_file.return_value = infected_result

            with patch(
                "media.services.quarantine.quarantine_infected_file"
            ) as mock_quarantine:
                mock_quarantine.return_value = MagicMock(success=True)

                with pytest.raises(Reject) as exc_info:
                    scan_file_for_malware(str(media_file_pending_scan.id))

                assert "Malware detected" in str(exc_info.value)
                assert "Eicar-Signature" in str(exc_info.value)

    def test_infected_file_calls_quarantine(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Infected file should be quarantined."""
        from media.services.scanner import MalwareScanResult
        from media.tasks import scan_file_for_malware

        infected_result = MalwareScanResult.infected(
            threat_name="TestVirus.A",
            scan_method="stream",
        )

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.scan_media_file.return_value = infected_result

            with patch(
                "media.services.quarantine.quarantine_infected_file"
            ) as mock_quarantine:
                mock_quarantine.return_value = MagicMock(success=True)

                with pytest.raises(Reject):
                    scan_file_for_malware(str(media_file_pending_scan.id))

                mock_quarantine.assert_called_once()
                call_args = mock_quarantine.call_args
                assert call_args[0][1] == "TestVirus.A"

    def test_skipped_returns_continue_dict(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Skipped scan (circuit open) should return dict to continue chain."""
        from media.services.scanner import MalwareScanResult
        from media.tasks import scan_file_for_malware

        skipped_result = MalwareScanResult.skipped(
            reason="Circuit breaker open - scanner unavailable"
        )

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.scan_media_file.return_value = skipped_result

            result = scan_file_for_malware(str(media_file_pending_scan.id))

            assert result["status"] == "skipped"
            assert "reason" in result

    def test_skipped_keeps_pending_status(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Skipped scan should keep scan_status as PENDING for rescan."""
        from media.services.scanner import MalwareScanResult
        from media.tasks import scan_file_for_malware

        skipped_result = MalwareScanResult.skipped(reason="Circuit breaker open")

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.scan_media_file.return_value = skipped_result

            scan_file_for_malware(str(media_file_pending_scan.id))

            media_file_pending_scan.refresh_from_db()
            assert media_file_pending_scan.scan_status == MediaFile.ScanStatus.PENDING

    def test_already_clean_skips_scan(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Already clean file should skip scanning."""
        from media.tasks import scan_file_for_malware

        # Mark as already clean
        media_file_pending_scan.scan_status = MediaFile.ScanStatus.CLEAN
        media_file_pending_scan.save()

        result = scan_file_for_malware(str(media_file_pending_scan.id))

        assert result["status"] == "already_scanned"

    def test_nonexistent_file_returns_not_found(self, db):
        """Nonexistent file should return not_found status."""
        from media.tasks import scan_file_for_malware

        result = scan_file_for_malware("00000000-0000-0000-0000-000000000000")

        assert result["status"] == "not_found"


class TestProcessMediaFileChainInput:
    """Test process_media_file accepts chain input."""

    def test_accepts_dict_from_scan(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Should accept dict from scan_file_for_malware."""
        from media.tasks import process_media_file

        scan_result = {
            "status": "clean",
            "media_file_id": str(media_file_pending_scan.id),
        }

        with patch("media.processors.image.generate_image_thumbnail"):
            result = process_media_file(scan_result)

            assert result["media_file_id"] == str(media_file_pending_scan.id)

    def test_accepts_direct_string(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Should accept direct string ID for backwards compatibility."""
        from media.tasks import process_media_file

        with patch("media.processors.image.generate_image_thumbnail"):
            result = process_media_file(str(media_file_pending_scan.id))

            assert result["media_file_id"] == str(media_file_pending_scan.id)

    def test_rejects_invalid_dict(self, db):
        """Should return error for dict missing media_file_id."""
        from media.tasks import process_media_file

        result = process_media_file({"status": "clean"})

        assert result["status"] == "error"
        assert "missing media_file_id" in result["error"]


class TestRescanSkippedFilesTask:
    """Test the rescan_skipped_files periodic task."""

    def test_queues_pending_files(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Should queue files with PENDING scan status and READY processing."""
        from media.tasks import rescan_skipped_files

        # Set up: file skipped scan but was processed
        media_file_pending_scan.scan_status = MediaFile.ScanStatus.PENDING
        media_file_pending_scan.processing_status = MediaFile.ProcessingStatus.READY
        media_file_pending_scan.save()

        with patch("media.tasks.scan_file_for_malware.delay") as mock_delay:
            result = rescan_skipped_files()

            mock_delay.assert_called_once_with(str(media_file_pending_scan.id))
            assert result["queued_count"] == 1

    def test_ignores_unprocessed_files(
        self, media_file_pending_scan: MediaFile, mock_clamav_scanner
    ):
        """Should not queue files that haven't finished processing."""
        from media.tasks import rescan_skipped_files

        # File hasn't been processed yet
        media_file_pending_scan.scan_status = MediaFile.ScanStatus.PENDING
        media_file_pending_scan.processing_status = MediaFile.ProcessingStatus.PENDING
        media_file_pending_scan.save()

        with patch("media.tasks.scan_file_for_malware.delay") as mock_delay:
            result = rescan_skipped_files()

            mock_delay.assert_not_called()
            assert result["queued_count"] == 0


class TestCheckAntivirusHealthTask:
    """Test the check_antivirus_health monitoring task."""

    def test_reports_healthy_scanner(self, mock_clamav_scanner):
        """Should report healthy when scanner is available."""
        from media.services.scanner import DefinitionInfo
        from media.tasks import check_antivirus_health
        from django.utils import timezone
        from datetime import timedelta

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.get_circuit_status.return_value = {"state": "closed"}
            mock_instance.is_available.return_value = True
            mock_instance.check_definitions.return_value = DefinitionInfo(
                version="ClamAV 1.2.0",
                signature_count=27234,
                last_update=timezone.now() - timedelta(hours=1),
            )

            result = check_antivirus_health()

            assert result["available"] is True
            assert result["circuit_state"] == "closed"
            assert len(result["warnings"]) == 0

    def test_warns_on_circuit_open(self, mock_clamav_scanner):
        """Should warn when circuit breaker is open."""
        from media.tasks import check_antivirus_health

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.get_circuit_status.return_value = {"state": "open"}
            mock_instance.is_available.return_value = False

            result = check_antivirus_health()

            assert result["circuit_state"] == "open"
            assert any("circuit breaker" in w.lower() for w in result["warnings"])

    def test_warns_on_unavailable_scanner(self, mock_clamav_scanner):
        """Should warn when scanner is unavailable."""
        from media.tasks import check_antivirus_health

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.get_circuit_status.return_value = {"state": "closed"}
            mock_instance.is_available.return_value = False

            result = check_antivirus_health()

            assert result["available"] is False
            assert any("not available" in w.lower() for w in result["warnings"])

    def test_warns_on_stale_definitions(self, mock_clamav_scanner):
        """Should warn when virus definitions are stale."""
        from media.services.scanner import DefinitionInfo
        from media.tasks import check_antivirus_health
        from django.utils import timezone
        from datetime import timedelta

        with patch("media.services.scanner.MalwareScanner") as MockScanner:
            mock_instance = MockScanner.return_value
            mock_instance.get_circuit_status.return_value = {"state": "closed"}
            mock_instance.is_available.return_value = True
            mock_instance.check_definitions.return_value = DefinitionInfo(
                version="ClamAV 1.2.0",
                signature_count=27234,
                # Definitions are 10 days old (stale by default 3-day threshold)
                last_update=timezone.now() - timedelta(days=10),
            )

            result = check_antivirus_health()

            assert result["available"] is True
            assert any("stale" in w.lower() for w in result["warnings"])
