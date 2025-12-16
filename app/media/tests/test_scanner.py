"""
Tests for the MalwareScanner service.

These tests verify:
- Clean file detection
- Infected file detection (EICAR test file)
- Stream vs path scanning based on file size
- Circuit breaker integration (fail-open behavior)
- Error handling
- Definition checking
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from media.models import MediaFile
from media.services.scanner import (
    MalwareScanner,
    MalwareScanResult,
    ScanResult,
)


# EICAR test string - standard antivirus test signature
# This is NOT malware - it's an industry-standard test pattern
EICAR_TEST_STRING = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test to ensure circuit breaker isolation."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def scanner():
    """Create a scanner instance for testing."""
    return MalwareScanner()


@pytest.fixture
def mock_clamd():
    """Create a mock ClamAV daemon connection."""
    mock = MagicMock()
    mock.ping.return_value = True
    mock.scan_stream.return_value = None  # Clean by default
    mock.scan_file.return_value = None  # Clean by default
    mock.version.return_value = "ClamAV 1.2.0/27234/Mon Dec 11 09:26:33 2024"
    return mock


@pytest.fixture
def clean_file_bytes() -> bytes:
    """Generate clean file content."""
    return b"This is a perfectly safe text file with no malware."


@pytest.fixture
def eicar_file_bytes() -> bytes:
    """Generate EICAR test file content."""
    return EICAR_TEST_STRING


@pytest.fixture
def temp_clean_file(clean_file_bytes: bytes) -> Path:
    """Create a temporary clean file on disk."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(clean_file_bytes)
        return Path(f.name)


@pytest.fixture
def temp_eicar_file(eicar_file_bytes: bytes) -> Path:
    """Create a temporary EICAR test file on disk."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(eicar_file_bytes)
        return Path(f.name)


class TestMalwareScanResult:
    """Test MalwareScanResult dataclass factory methods."""

    def test_clean_result(self):
        """Clean result should have correct status."""
        result = MalwareScanResult.clean(scan_method="stream")

        assert result.status == ScanResult.CLEAN
        assert result.threat_name is None
        assert result.skipped_reason is None
        assert result.error_message is None
        assert result.scan_method == "stream"
        assert result.scanned_at is not None

    def test_infected_result(self):
        """Infected result should include threat name."""
        result = MalwareScanResult.infected("Eicar-Signature", scan_method="path")

        assert result.status == ScanResult.INFECTED
        assert result.threat_name == "Eicar-Signature"
        assert result.skipped_reason is None
        assert result.scan_method == "path"
        assert result.scanned_at is not None

    def test_error_result(self):
        """Error result should include message."""
        result = MalwareScanResult.error("Connection failed")

        assert result.status == ScanResult.ERROR
        assert result.error_message == "Connection failed"
        assert result.threat_name is None
        assert result.scanned_at is not None

    def test_skipped_result(self):
        """Skipped result should include reason."""
        result = MalwareScanResult.skipped("Circuit breaker open")

        assert result.status == ScanResult.SKIPPED
        assert result.skipped_reason == "Circuit breaker open"
        assert result.threat_name is None
        assert result.scanned_at is not None


class TestMalwareScannerStreamScan:
    """Test stream-based scanning."""

    def test_scan_stream_clean_file(
        self, scanner: MalwareScanner, mock_clamd, clean_file_bytes: bytes
    ):
        """Clean file should return CLEAN status."""
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.scan_stream.return_value = None

            result = scanner.scan_stream(clean_file_bytes)

            assert result.status == ScanResult.CLEAN
            assert result.scan_method == "stream"
            mock_clamd.scan_stream.assert_called_once_with(clean_file_bytes)

    def test_scan_stream_infected_file(
        self, scanner: MalwareScanner, mock_clamd, eicar_file_bytes: bytes
    ):
        """Infected file should return INFECTED status with threat name."""
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.scan_stream.return_value = {
                "stream": ("FOUND", "Eicar-Signature")
            }

            result = scanner.scan_stream(eicar_file_bytes)

            assert result.status == ScanResult.INFECTED
            assert result.threat_name == "Eicar-Signature"
            assert result.scan_method == "stream"

    def test_scan_stream_records_success(
        self, scanner: MalwareScanner, mock_clamd, clean_file_bytes: bytes
    ):
        """Successful scan should record success with circuit breaker."""
        with patch.object(scanner, "_clamd", mock_clamd):
            with patch.object(scanner._circuit, "record_success") as mock_record:
                scanner.scan_stream(clean_file_bytes)
                mock_record.assert_called_once()

    def test_scan_stream_connection_error(
        self, scanner: MalwareScanner, mock_clamd, clean_file_bytes: bytes
    ):
        """Connection error should return ERROR status and record failure."""
        import pyclamd

        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.scan_stream.side_effect = pyclamd.ConnectionError(
                "Connection refused"
            )

            with patch.object(scanner._circuit, "record_failure") as mock_failure:
                result = scanner.scan_stream(clean_file_bytes)

                assert result.status == ScanResult.ERROR
                assert "Connection error" in result.error_message
                mock_failure.assert_called_once()


class TestMalwareScannerPathScan:
    """Test file path-based scanning."""

    def test_scan_file_path_clean(
        self, scanner: MalwareScanner, mock_clamd, temp_clean_file: Path
    ):
        """Clean file should return CLEAN status."""
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.scan_file.return_value = None

            result = scanner.scan_file_path(temp_clean_file)

            assert result.status == ScanResult.CLEAN
            assert result.scan_method == "path"

        # Cleanup
        temp_clean_file.unlink(missing_ok=True)

    def test_scan_file_path_infected(
        self, scanner: MalwareScanner, mock_clamd, temp_eicar_file: Path
    ):
        """Infected file should return INFECTED status."""
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.scan_file.return_value = {
                str(temp_eicar_file): ("FOUND", "Eicar-Signature")
            }

            result = scanner.scan_file_path(temp_eicar_file)

            assert result.status == ScanResult.INFECTED
            assert result.threat_name == "Eicar-Signature"
            assert result.scan_method == "path"

        # Cleanup
        temp_eicar_file.unlink(missing_ok=True)

    def test_scan_file_path_not_found(self, scanner: MalwareScanner, mock_clamd):
        """Non-existent file should return ERROR status."""
        with patch.object(scanner, "_clamd", mock_clamd):
            result = scanner.scan_file_path("/nonexistent/path/to/file.txt")

            assert result.status == ScanResult.ERROR
            assert "File not found" in result.error_message


class TestMalwareScannerCircuitBreaker:
    """Test circuit breaker integration."""

    def test_skip_scan_when_circuit_open(
        self, scanner: MalwareScanner, clean_file_bytes: bytes
    ):
        """Should skip scan and return SKIPPED when circuit is open."""
        # Open the circuit by simulating failures
        with patch.object(scanner._circuit, "is_available", return_value=False):
            result = scanner.scan_stream(clean_file_bytes)

            assert result.status == ScanResult.SKIPPED
            assert "Circuit breaker open" in result.skipped_reason

    def test_circuit_opens_after_failures(
        self, scanner: MalwareScanner, mock_clamd, clean_file_bytes: bytes
    ):
        """Circuit should open after threshold failures."""
        import pyclamd

        # Simulate repeated connection failures
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.scan_stream.side_effect = pyclamd.ConnectionError(
                "Connection refused"
            )

            # Trigger failures to open circuit (default threshold is 5)
            for _ in range(5):
                scanner.scan_stream(clean_file_bytes)

            # Circuit should now be open
            assert scanner._circuit.get_status()["state"] == "open"

            # Next scan should be skipped without trying
            result = scanner.scan_stream(clean_file_bytes)
            assert result.status == ScanResult.SKIPPED


class TestMalwareScannerMediaFile:
    """Test scanning MediaFile instances."""

    @pytest.fixture
    def mock_media_file(self, temp_clean_file: Path):
        """Create a mock MediaFile instance."""
        mock = MagicMock(spec=MediaFile)
        mock.id = "test-uuid"
        mock.file.path = str(temp_clean_file)
        mock.file_size = 100  # Small file
        return mock

    @pytest.fixture
    def mock_large_media_file(self, temp_clean_file: Path):
        """Create a mock large MediaFile instance."""
        mock = MagicMock(spec=MediaFile)
        mock.id = "test-uuid-large"
        mock.file.path = str(temp_clean_file)
        mock.file_size = 100 * 1024 * 1024  # 100MB - large file
        return mock

    def test_small_file_uses_stream_scan(
        self,
        scanner: MalwareScanner,
        mock_clamd,
        mock_media_file,
        temp_clean_file: Path,
    ):
        """Small files should use stream scanning."""
        with patch.object(scanner, "_clamd", mock_clamd):
            with patch.object(scanner, "scan_stream", wraps=scanner.scan_stream):
                scanner.scan_media_file(mock_media_file)
                # Note: scan_stream is called internally, not mocked directly
                # Check that scan_file was NOT called
                mock_clamd.scan_file.assert_not_called()

        temp_clean_file.unlink(missing_ok=True)

    def test_large_file_uses_path_scan(
        self,
        scanner: MalwareScanner,
        mock_clamd,
        mock_large_media_file,
        temp_clean_file: Path,
    ):
        """Large files should use path scanning."""
        with patch.object(scanner, "_clamd", mock_clamd):
            result = scanner.scan_media_file(mock_large_media_file)

            mock_clamd.scan_file.assert_called_once()
            assert result.scan_method == "path"

        temp_clean_file.unlink(missing_ok=True)


class TestMalwareScannerDefinitions:
    """Test virus definition checking."""

    def test_check_definitions_success(self, scanner: MalwareScanner, mock_clamd):
        """Should parse version info correctly."""
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.version.return_value = (
                "ClamAV 1.2.0/27234/Mon Dec 11 09:26:33 2024"
            )

            result = scanner.check_definitions()

            assert result is not None
            assert "ClamAV 1.2.0" in result.version
            assert result.signature_count == 27234
            assert result.last_update is not None

    def test_check_definitions_failure(self, scanner: MalwareScanner, mock_clamd):
        """Should return None on failure."""
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.version.side_effect = Exception("Connection error")

            result = scanner.check_definitions()

            assert result is None


class TestMalwareScannerAvailability:
    """Test scanner availability checks."""

    def test_is_available_when_connected(self, scanner: MalwareScanner, mock_clamd):
        """Should return True when ClamAV is connected."""
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.ping.return_value = True

            assert scanner.is_available() is True

    def test_is_available_when_circuit_open(self, scanner: MalwareScanner):
        """Should return False when circuit is open."""
        with patch.object(scanner._circuit, "is_available", return_value=False):
            assert scanner.is_available() is False

    def test_is_available_when_connection_fails(
        self, scanner: MalwareScanner, mock_clamd
    ):
        """Should return False when ping fails."""
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.ping.side_effect = Exception("Connection refused")

            assert scanner.is_available() is False


class TestMalwareScannerReset:
    """Test circuit breaker reset functionality."""

    def test_reset_circuit(
        self, scanner: MalwareScanner, mock_clamd, clean_file_bytes: bytes
    ):
        """Should be able to manually reset the circuit."""
        import pyclamd

        # Open the circuit through failures
        with patch.object(scanner, "_clamd", mock_clamd):
            mock_clamd.scan_stream.side_effect = pyclamd.ConnectionError("fail")

            for _ in range(5):
                scanner.scan_stream(clean_file_bytes)

            assert scanner._circuit.get_status()["state"] == "open"

            # Reset the circuit
            scanner.reset_circuit()

            assert scanner._circuit.get_status()["state"] == "closed"
