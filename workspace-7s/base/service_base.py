"""
Abstract service base classes for workspace-7s.

All services inherit from ServiceBase or specialized bases to ensure
consistent interface and behavior.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime
import logging
import time


class ServiceBase(ABC):
    """
    Abstract base class for workspace-7s services.

    Interface contract:
    - Each service implements execute() (sync)
    - Services can be region-specific (CN vs US)
    - Services report status and results
    """

    def __init__(
        self,
        service_name: str,
        region: str,
        config: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        self.service_name = service_name
        self.region = region
        self.config = config or {}
        self.logger = self._setup_logger()
        self.execution_count = 0
        self.last_execution_time: Optional[datetime] = None
        self.last_result: Optional[Dict] = None

    def _setup_logger(self) -> logging.Logger:
        """Setup logger for this service."""
        logger = logging.getLogger(f"7s.{self.service_name}.{self.region}")
        return logger

    def execute(self, **kwargs) -> Dict[str, Any]:
        """
        Execute the service (sync).

        Args:
            **kwargs: Service-specific parameters

        Returns:
            Dictionary with results:
            {
                "status": "success" or "error",
                "service": service_name,
                "region": region,
                "timestamp": execution_time,
                "data": result_data,
                "error": error_message if failed,
                "execution_time_ms": duration,
            }
        """
        start_time = datetime.now()
        self.execution_count += 1

        try:
            self.logger.info(f"Executing {self.service_name} for {self.region}")

            # Call subclass implementation
            result = self._execute_impl(**kwargs)

            execution_time = (datetime.now() - start_time).total_seconds() * 1000

            response = {
                "status": "success",
                "service": self.service_name,
                "region": self.region,
                "timestamp": datetime.now().isoformat(),
                "data": result,
                "execution_time_ms": execution_time,
                "execution_count": self.execution_count,
            }

            self.last_execution_time = datetime.now()
            self.last_result = response

            self.logger.info(f"✓ {self.service_name} completed in {execution_time:.0f}ms")
            return response

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds() * 1000

            self.logger.error(f"✗ {self.service_name} failed: {str(e)}", exc_info=True)

            return {
                "status": "error",
                "service": self.service_name,
                "region": self.region,
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "execution_time_ms": execution_time,
                "execution_count": self.execution_count,
            }

    @abstractmethod
    def _execute_impl(self, **kwargs) -> Dict[str, Any]:
        """
        Subclass-specific implementation of execution.

        Args:
            **kwargs: Service-specific parameters

        Returns:
            Service-specific result data
        """
        pass

    def get_status(self) -> Dict[str, Any]:
        """Get current status of service."""
        return {
            "service": self.service_name,
            "region": self.region,
            "execution_count": self.execution_count,
            "last_execution_time": self.last_execution_time.isoformat() if self.last_execution_time else None,
            "last_status": self.last_result.get("status") if self.last_result else None,
        }

    def validate_config(self) -> bool:
        """
        Validate that required config is present.

        Override in subclass to add specific checks.

        Returns:
            True if config is valid
        """
        required_keys = ["market_hours"]
        for key in required_keys:
            if key not in self.config:
                self.logger.warning(f"Missing config key: {key}")
                return False
        return True
