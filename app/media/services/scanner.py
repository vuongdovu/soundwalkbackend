"""
Malware scanning service using ClamAV.

This module provides a resilient malware scanner that:
- Connects to ClamAV daemon via pyclamd
- Uses circuit breaker for fail-open behavior during outages
- Supports both stream and file path scanning based on file size
- Tracks virus definition freshness

Usage:
    from media.services.scanner import MalwareScanner

    scanner = MalwareScanner()

    # Scan a file path
    result = scanner.scan_file_path("/path/to/file")

    # Scan a MediaFile instance
    result = scanner.scan_media_file(media_file)

    if result.status == ScanResult.CLEAN:
        # File is safe
    elif result.status == ScanResult.INFECTED:
        # File contains malware
        print(f"Threat: {result.threat_name}")
    elif result.status == ScanResult.SKIPPED:
        # Scanner unavailable (circuit open)
        print(f"Skipped: {result.skipped_reason}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import pyclamd
from django.conf import settings
from django.utils import timezone

from core.circuit_breaker import CircuitBreaker, CircuitOpenError

if TYPE_CHECKING:
    from media.models import MediaFile

logger = logging.getLogger(__name__)


class ScanResult(str, Enum):
    """Result status of a malware scan."""

    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class MalwareScanResult:
    """
    Result of a malware scan operation.

    Attributes:
        status: The scan result status (clean, infected, error, skipped).
        threat_name: Name of detected threat if infected.
        skipped_reason: Reason for skipping if status is SKIPPED.
        error_message: Error details if status is ERROR.
        scanned_at: Timestamp when scan completed.
        scan_method: How the scan was performed (stream, path).
    """

    status: ScanResult
    threat_name: str | None = None
    skipped_reason: str | None = None
    error_message: str | None = None
    scanned_at: datetime | None = None
    scan_method: str | None = None

    @classmethod
    def clean(cls, scan_method: str = "stream") -> MalwareScanResult:
        """Create a clean scan result."""
        return cls(
            status=ScanResult.CLEAN,
            scanned_at=timezone.now(),
            scan_method=scan_method,
        )

    @classmethod
    def infected(
        cls, threat_name: str, scan_method: str = "stream"
    ) -> MalwareScanResult:
        """Create an infected scan result."""
        return cls(
            status=ScanResult.INFECTED,
            threat_name=threat_name,
            scanned_at=timezone.now(),
            scan_method=scan_method,
        )

    @classmethod
    def error(cls, message: str) -> MalwareScanResult:
        """Create an error scan result."""
        return cls(
            status=ScanResult.ERROR,
            error_message=message,
            scanned_at=timezone.now(),
        )

    @classmethod
    def skipped(cls, reason: str) -> MalwareScanResult:
        """Create a skipped scan result."""
        return cls(
            status=ScanResult.SKIPPED,
            skipped_reason=reason,
            scanned_at=timezone.now(),
        )


@dataclass
class DefinitionInfo:
    """ClamAV virus definition information."""

    version: str
    signature_count: int
    last_update: datetime | None


class MalwareScanner:
    """
    Malware scanner using ClamAV daemon.

    Features:
    - Lazy connection to ClamAV daemon
    - Circuit breaker for fail-open behavior
    - Stream scanning for small files (memory efficient)
    - Path scanning for large files (disk read)
    - Definition freshness monitoring

    Example:
        scanner = MalwareScanner()

        # Check if scanner is available
        if scanner.is_available():
            result = scanner.scan_file_path("/path/to/file")
            if result.status == ScanResult.INFECTED:
                quarantine_file(result.threat_name)
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        timeout: int | None = None,
    ):
        """
        Initialize the malware scanner.

        Args:
            host: ClamAV daemon host (default from settings).
            port: ClamAV daemon port (default from settings).
            timeout: Connection timeout in seconds (default from settings).
        """
        self._host = host or settings.CLAMAV_HOST
        self._port = port or settings.CLAMAV_PORT
        self._timeout = timeout or settings.CLAMAV_TIMEOUT

        # Lazy-initialized connection
        self._clamd: pyclamd.ClamdNetworkSocket | None = None

        # Circuit breaker for resilience
        self._circuit = CircuitBreaker(
            name="clamav",
            failure_threshold=settings.CLAMAV_CIRCUIT_FAILURE_THRESHOLD,
            recovery_timeout=settings.CLAMAV_CIRCUIT_RECOVERY_TIMEOUT,
        )

        # Large file threshold in bytes
        self._large_file_threshold = (
            settings.CLAMAV_LARGE_FILE_THRESHOLD_MB * 1024 * 1024
        )

    @property
    def clamd(self) -> pyclamd.ClamdNetworkSocket:
        """
        Get the ClamAV daemon connection (lazy initialization).

        Returns:
            Connected ClamdNetworkSocket instance.

        Raises:
            ConnectionError: If unable to connect to ClamAV daemon.
        """
        if self._clamd is None:
            self._clamd = pyclamd.ClamdNetworkSocket(
                host=self._host,
                port=self._port,
                timeout=self._timeout,
            )
            # Verify connection
            if not self._clamd.ping():
                self._clamd = None
                raise ConnectionError(
                    f"Unable to connect to ClamAV daemon at {self._host}:{self._port}"
                )
        return self._clamd

    def is_available(self) -> bool:
        """
        Check if the scanner is available for scanning.

        This checks both the circuit breaker state and the actual
        ClamAV daemon connectivity.

        Returns:
            True if scanner is ready to accept scans.
        """
        if not self._circuit.is_available():
            return False

        try:
            return self.clamd.ping()
        except Exception:
            return False

    def scan_stream(self, data: bytes) -> MalwareScanResult:
        """
        Scan data in memory via ClamAV stream.

        Use this for smaller files that can fit in memory.
        For large files, use scan_file_path() instead.

        Args:
            data: File content as bytes.

        Returns:
            MalwareScanResult with scan outcome.
        """
        if not self._circuit.is_available():
            logger.warning(
                "Malware scan skipped: circuit breaker open",
                extra={"circuit_status": self._circuit.get_status()},
            )
            return MalwareScanResult.skipped(
                "Circuit breaker open - scanner unavailable"
            )

        try:
            result = self.clamd.scan_stream(data)
            self._circuit.record_success()

            if result is None:
                # No malware detected
                return MalwareScanResult.clean(scan_method="stream")

            # Result format: {'stream': ('FOUND', 'ThreatName')}
            status, threat_name = result.get("stream", (None, None))
            if status == "FOUND":
                logger.warning(
                    "Malware detected in stream scan",
                    extra={"threat_name": threat_name},
                )
                return MalwareScanResult.infected(threat_name, scan_method="stream")

            # Unexpected result format
            return MalwareScanResult.clean(scan_method="stream")

        except CircuitOpenError:
            return MalwareScanResult.skipped(
                "Circuit breaker open - scanner unavailable"
            )
        except pyclamd.ConnectionError as e:
            self._circuit.record_failure()
            logger.error(
                f"ClamAV connection error during stream scan: {e}",
                extra={"host": self._host, "port": self._port},
            )
            return MalwareScanResult.error(f"Connection error: {e}")
        except Exception as e:
            self._circuit.record_failure()
            logger.exception(
                f"Unexpected error during stream scan: {e}",
            )
            return MalwareScanResult.error(f"Scan error: {e}")

    def scan_file_path(self, file_path: str | Path) -> MalwareScanResult:
        """
        Scan a file by path via ClamAV.

        Use this for large files to avoid loading them into memory.
        ClamAV reads the file directly from disk.

        Args:
            file_path: Path to the file to scan.

        Returns:
            MalwareScanResult with scan outcome.
        """
        if not self._circuit.is_available():
            logger.warning(
                "Malware scan skipped: circuit breaker open",
                extra={
                    "file_path": str(file_path),
                    "circuit_status": self._circuit.get_status(),
                },
            )
            return MalwareScanResult.skipped(
                "Circuit breaker open - scanner unavailable"
            )

        file_path = Path(file_path)
        if not file_path.exists():
            return MalwareScanResult.error(f"File not found: {file_path}")

        try:
            result = self.clamd.scan_file(str(file_path))
            self._circuit.record_success()

            if result is None:
                # No malware detected
                return MalwareScanResult.clean(scan_method="path")

            # Result format: {'/path/to/file': ('FOUND', 'ThreatName')}
            file_result = result.get(str(file_path))
            if file_result:
                status, threat_name = file_result
                if status == "FOUND":
                    logger.warning(
                        "Malware detected in file scan",
                        extra={
                            "file_path": str(file_path),
                            "threat_name": threat_name,
                        },
                    )
                    return MalwareScanResult.infected(threat_name, scan_method="path")

            # No malware found
            return MalwareScanResult.clean(scan_method="path")

        except CircuitOpenError:
            return MalwareScanResult.skipped(
                "Circuit breaker open - scanner unavailable"
            )
        except pyclamd.ConnectionError as e:
            self._circuit.record_failure()
            logger.error(
                f"ClamAV connection error during file scan: {e}",
                extra={
                    "file_path": str(file_path),
                    "host": self._host,
                    "port": self._port,
                },
            )
            return MalwareScanResult.error(f"Connection error: {e}")
        except Exception as e:
            self._circuit.record_failure()
            logger.exception(
                f"Unexpected error during file scan: {e}",
                extra={"file_path": str(file_path)},
            )
            return MalwareScanResult.error(f"Scan error: {e}")

    def scan_media_file(self, media_file: MediaFile) -> MalwareScanResult:
        """
        Scan a MediaFile instance.

        Automatically chooses between stream and path scanning
        based on file size:
        - Small files (<threshold): Stream scan (loads into memory)
        - Large files (>=threshold): Path scan (reads from disk)

        Args:
            media_file: The MediaFile instance to scan.

        Returns:
            MalwareScanResult with scan outcome.
        """
        file_path = media_file.file.path
        file_size = media_file.file_size

        if file_size >= self._large_file_threshold:
            logger.debug(
                f"Using path scan for large file ({file_size} bytes)",
                extra={
                    "media_file_id": str(media_file.id),
                    "file_size": file_size,
                    "threshold": self._large_file_threshold,
                },
            )
            return self.scan_file_path(file_path)
        else:
            # Stream scan for smaller files
            try:
                with open(file_path, "rb") as f:
                    data = f.read()
                return self.scan_stream(data)
            except OSError as e:
                logger.error(
                    f"Failed to read file for stream scan: {e}",
                    extra={"media_file_id": str(media_file.id)},
                )
                return MalwareScanResult.error(f"File read error: {e}")

    def check_definitions(self) -> DefinitionInfo | None:
        """
        Check ClamAV virus definition version and freshness.

        Returns:
            DefinitionInfo with version details, or None if unavailable.
        """
        try:
            version_info = self.clamd.version()
            # Version string format: "ClamAV 1.2.0/27234/Mon Dec 11 09:26:33 2024"
            if version_info:
                parts = version_info.split("/")
                return DefinitionInfo(
                    version=parts[0] if len(parts) > 0 else "unknown",
                    signature_count=int(parts[1]) if len(parts) > 1 else 0,
                    last_update=self._parse_definition_date(parts[2])
                    if len(parts) > 2
                    else None,
                )
            return None
        except Exception as e:
            logger.error(f"Failed to check ClamAV definitions: {e}")
            return None

    def _parse_definition_date(self, date_str: str) -> datetime | None:
        """Parse ClamAV definition date string."""
        try:
            # Format: "Mon Dec 11 09:26:33 2024"
            from datetime import datetime as dt

            return timezone.make_aware(
                dt.strptime(date_str.strip(), "%a %b %d %H:%M:%S %Y")
            )
        except (ValueError, TypeError):
            return None

    def get_circuit_status(self) -> dict:
        """Get the circuit breaker status for monitoring."""
        return self._circuit.get_status()

    def reset_circuit(self) -> None:
        """Manually reset the circuit breaker (for admin use)."""
        self._circuit.reset()
        logger.info("ClamAV circuit breaker manually reset")
