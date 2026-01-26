"""
File Manager - Handles file operations for downloaded reports.

Provides utilities for:
- File naming with timestamps
- File validation
- Cleanup of old files
"""

import os
import shutil
from datetime import datetime
from typing import Optional
import structlog

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class FileManager:
    """Manages file operations for downloaded reports."""
    
    def __init__(self):
        self.download_dir = settings.download_dir
        self.screenshot_dir = settings.screenshot_dir
        self._ensure_directories()
    
    def _ensure_directories(self) -> None:
        """Ensure required directories exist."""
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(self.screenshot_dir, exist_ok=True)
        logger.debug("Directories verified", 
                    download_dir=self.download_dir,
                    screenshot_dir=self.screenshot_dir)
    
    def generate_filename(
        self,
        report_type: str,
        tenant_name: str,
        period: Optional[str] = None,
        extension: str = "xlsx"
    ) -> str:
        """
        Generate a standardized filename for a report.
        
        Args:
            report_type: Type of report (e.g., 'activity_statement', 'payroll_summary')
            tenant_name: Name of the Xero tenant
            period: Optional period string (e.g., '2026-01', 'Q4_2025')
            extension: File extension without dot
            
        Returns:
            Generated filename
        """
        # Sanitize tenant name for filesystem
        safe_tenant = self._sanitize_filename(tenant_name)
        
        # Format report type
        report_name = report_type.replace("_", " ").title().replace(" ", "_")
        
        # Build filename components
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if period:
            filename = f"{report_name}_{safe_tenant}_{period}_{timestamp}.{extension}"
        else:
            filename = f"{report_name}_{safe_tenant}_{timestamp}.{extension}"
        
        return filename
    
    def _sanitize_filename(self, name: str) -> str:
        """
        Sanitize a string for use in filenames.
        
        Removes or replaces characters that are invalid in filenames.
        """
        # Characters to remove
        invalid_chars = '<>:"/\\|?*'
        
        result = name
        for char in invalid_chars:
            result = result.replace(char, '')
        
        # Replace spaces with underscores
        result = result.replace(' ', '_')
        
        # Remove consecutive underscores
        while '__' in result:
            result = result.replace('__', '_')
        
        # Trim to reasonable length
        return result[:100]
    
    def rename_download(
        self,
        original_path: str,
        new_filename: str
    ) -> str:
        """
        Rename a downloaded file.
        
        Args:
            original_path: Path to the original file
            new_filename: New filename (without directory)
            
        Returns:
            Path to the renamed file
        """
        if not os.path.exists(original_path):
            raise FileNotFoundError(f"File not found: {original_path}")
        
        new_path = os.path.join(self.download_dir, new_filename)
        
        # Handle existing file
        if os.path.exists(new_path):
            logger.warning("File already exists, will overwrite", path=new_path)
            os.remove(new_path)
        
        shutil.move(original_path, new_path)
        logger.info("File renamed", original=original_path, new=new_path)
        
        return new_path
    
    def get_file_info(self, filepath: str) -> dict:
        """
        Get information about a file.
        
        Returns:
            Dict with file metadata
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        
        stat = os.stat(filepath)
        
        return {
            "path": filepath,
            "filename": os.path.basename(filepath),
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
    
    def list_downloads(self) -> list[dict]:
        """
        List all files in the download directory.
        
        Returns:
            List of file info dicts
        """
        files = []
        
        for filename in os.listdir(self.download_dir):
            filepath = os.path.join(self.download_dir, filename)
            if os.path.isfile(filepath):
                try:
                    files.append(self.get_file_info(filepath))
                except Exception as e:
                    logger.warning("Error getting file info", 
                                  filename=filename, error=str(e))
        
        return sorted(files, key=lambda x: x["modified_at"], reverse=True)
    
    def cleanup_old_files(self, max_age_days: int = 30) -> int:
        """
        Remove files older than specified age.
        
        Args:
            max_age_days: Maximum age in days before deletion
            
        Returns:
            Number of files deleted
        """
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(days=max_age_days)
        deleted = 0
        
        for filename in os.listdir(self.download_dir):
            filepath = os.path.join(self.download_dir, filename)
            if os.path.isfile(filepath):
                mtime = datetime.fromtimestamp(os.stat(filepath).st_mtime)
                if mtime < cutoff:
                    os.remove(filepath)
                    deleted += 1
                    logger.info("Deleted old file", filename=filename, age_days=(datetime.now() - mtime).days)
        
        return deleted
    
    def validate_excel_file(self, filepath: str) -> bool:
        """
        Validate that a file appears to be a valid Excel file.
        
        Args:
            filepath: Path to the file
            
        Returns:
            True if file appears valid
        """
        if not os.path.exists(filepath):
            return False
        
        # Check file size (should be at least a few KB for Excel)
        size = os.stat(filepath).st_size
        if size < 1000:  # Less than 1KB is suspicious
            logger.warning("File too small to be valid Excel", 
                          filepath=filepath, size=size)
            return False
        
        # Check file extension
        if not filepath.lower().endswith(('.xlsx', '.xls')):
            logger.warning("File does not have Excel extension", filepath=filepath)
            return False
        
        # Check magic bytes for xlsx (ZIP format)
        try:
            with open(filepath, 'rb') as f:
                header = f.read(4)
                # XLSX files are ZIP archives starting with PK
                if header[:2] != b'PK':
                    logger.warning("File does not have valid Excel header", filepath=filepath)
                    return False
        except Exception as e:
            logger.error("Error reading file", filepath=filepath, error=str(e))
            return False
        
        return True


# Singleton instance
_file_manager: FileManager = None


def get_file_manager() -> FileManager:
    """Get the singleton file manager instance."""
    global _file_manager
    if _file_manager is None:
        _file_manager = FileManager()
    return _file_manager
