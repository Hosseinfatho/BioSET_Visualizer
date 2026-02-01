from __future__ import annotations

import sqlite3
import gzip
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AnalysisMetadata:
    channels: list[str]
    hierarchy_levels: list[dict]
    dilation_amounts: list[int]
    volume_bounds: dict


@dataclass
class TileData:
    """Spatial tile with overlap count."""
    x0: int
    x1: int
    y0: int
    y1: int
    count: int


@dataclass
class CombinationData:
    """A biomarker combination with its overlap data."""
    channels: list[str]
    total_count: int
    tiles: list[TileData] = field(default_factory=list)


class AnalysisLoader:
    """
    Loader and query interface for .bioset analysis files.
    
    Usage:
        loader = AnalysisLoader()
        loader.load("/path/to/analysis.bioset")
        
        # Get metadata
        print(loader.metadata.channels)
        print(loader.metadata.dilation_amounts)
        
        # Query combinations
        top_combos = loader.get_top_combinations(dilation=0, hierarchy_level=2, limit=50)
        
        # Get tiles for heatmap
        tiles = loader.get_combination_tiles(["Hoechst", "MART1"], dilation=0, hierarchy_level=1)
    """
    
    def __init__(self):
        self._conn: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None
        self._temp_dir: Optional[str] = None
        self.metadata: Optional[AnalysisMetadata] = None
        self._loaded = False
    
    @property
    def is_loaded(self) -> bool:
        return self._loaded and self._conn is not None
    
    def load(self, file_path: str) -> AnalysisMetadata:
        """
        Load a .bioset analysis file.
        
        Args:
            file_path: Path to the .bioset file (gzipped SQLite)
            
        Returns:
            AnalysisMetadata with channels, hierarchy levels, etc.
        """
        # Close any existing connection
        self.close()
        
        # Decompress to temp file
        self._temp_dir = tempfile.mkdtemp(prefix="bioset_analysis_")
        self._db_path = Path(self._temp_dir) / "analysis.db"
        
        print(f"[analysis] Loading {file_path}...")
        
        with gzip.open(file_path, 'rb') as f_in:
            with open(self._db_path, 'wb') as f_out:
                f_out.write(f_in.read())
        
        # Open connection
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        
        # Load metadata
        cursor = self._conn.execute("SELECT key, value FROM metadata")
        meta_dict = {row["key"]: json.loads(row["value"]) for row in cursor}
        
        self.metadata = AnalysisMetadata(
            channels=meta_dict.get("channels", []),
            hierarchy_levels=meta_dict.get("hierarchy_levels", []),
            dilation_amounts=meta_dict.get("dilation_amounts", []),
            volume_bounds=meta_dict.get("volume_bounds", {}),
        )
        
        self._loaded = True
        
        print(f"[analysis] Loaded: {len(self.metadata.channels)} channels, "
              f"{len(self.metadata.dilation_amounts)} dilations, "
              f"{len(self.metadata.hierarchy_levels)} hierarchy levels")
        
        return self.metadata
    
    def load_from_bytes(self, data: bytes) -> AnalysisMetadata:
        """
        Load from raw bytes (for file upload handling).
        
        Args:
            data: Raw bytes of the .bioset file
            
        Returns:
            AnalysisMetadata
        """
        # Close any existing connection
        self.close()
        
        # Write to temp file and decompress
        self._temp_dir = tempfile.mkdtemp(prefix="bioset_analysis_")
        compressed_path = Path(self._temp_dir) / "uploaded.bioset"
        self._db_path = Path(self._temp_dir) / "analysis.db"
        
        # Write compressed data
        with open(compressed_path, 'wb') as f:
            f.write(data)
        
        print(f"[analysis] Loading from uploaded bytes ({len(data)} bytes)...")
        
        # Decompress
        with gzip.open(compressed_path, 'rb') as f_in:
            with open(self._db_path, 'wb') as f_out:
                f_out.write(f_in.read())
        
        # Open connection
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        
        # Load metadata
        cursor = self._conn.execute("SELECT key, value FROM metadata")
        meta_dict = {row["key"]: json.loads(row["value"]) for row in cursor}
        
        self.metadata = AnalysisMetadata(
            channels=meta_dict.get("channels", []),
            hierarchy_levels=meta_dict.get("hierarchy_levels", []),
            dilation_amounts=meta_dict.get("dilation_amounts", []),
            volume_bounds=meta_dict.get("volume_bounds", {}),
        )
        
        self._loaded = True
        
        print(f"[analysis] Loaded: {len(self.metadata.channels)} channels, "
              f"{len(self.metadata.dilation_amounts)} dilations, "
              f"{len(self.metadata.hierarchy_levels)} hierarchy levels")
        
        return self.metadata
    
    def get_top_combinations(
        self,
        dilation: int,
        hierarchy_level: int,
        limit: int = 50,
        min_channels: int = 1,
    ) -> list[CombinationData]:
        """Get top N combinations by total overlap count."""
        if not self.is_loaded:
            return []
        
        cursor = self._conn.execute('''
            SELECT channels, total_count 
            FROM combinations 
            WHERE dilation = ? AND hierarchy_level = ? AND channel_count >= ?
            ORDER BY total_count DESC 
            LIMIT ?
        ''', (dilation, hierarchy_level, min_channels, limit))
        
        results = []
        for row in cursor:
            channels_str = row["channels"]
            channels = channels_str.split("|") if channels_str else []
            results.append(CombinationData(
                channels=channels,
                total_count=row["total_count"],
            ))
        
        return results
    
    def get_filtered_combinations(
        self,
        channel_filter: list[str],
        dilation: int,
        hierarchy_level: int,
        limit: int = 50,
        exact_match: bool = False,
    ) -> list[CombinationData]:
        # combinations containing specified channels.
        if not self.is_loaded or not channel_filter:
            return []
        
        if exact_match:
            channels_str = "|".join(sorted(channel_filter))
            cursor = self._conn.execute('''
                SELECT channels, total_count 
                FROM combinations 
                WHERE channels = ? AND dilation = ? AND hierarchy_level = ?
            ''', (channels_str, dilation, hierarchy_level))
        else:
            query = '''
                SELECT channels, total_count 
                FROM combinations 
                WHERE dilation = ? AND hierarchy_level = ?
            '''
            params = [dilation, hierarchy_level]
            
            for ch in channel_filter:
                query += " AND channels LIKE ?"
                params.append(f"%{ch}%")
            
            query += " ORDER BY total_count DESC LIMIT ?"
            params.append(limit)
            
            cursor = self._conn.execute(query, params)
        
        results = []
        for row in cursor:
            channels_str = row["channels"]
            channels = channels_str.split("|") if channels_str else []
            results.append(CombinationData(
                channels=channels,
                total_count=row["total_count"],
            ))
        
        return results
    
    def get_combination_tiles(
        self,
        channels: list[str],
        dilation: int,
        hierarchy_level: int,
    ) -> list[TileData]:
        # tiles for a specific combination.
        if not self.is_loaded:
            return []
        
        channels_str = "|".join(sorted(channels))
        
        cursor = self._conn.execute('''
            SELECT t.tile_x0, t.tile_x1, t.tile_y0, t.tile_y1, t.count
            FROM combinations c
            JOIN tiles t ON c.id = t.combination_id
            WHERE c.channels = ? AND c.dilation = ? AND c.hierarchy_level = ?
            ORDER BY t.count DESC
        ''', (channels_str, dilation, hierarchy_level))
        
        return [
            TileData(
                x0=row["tile_x0"],
                x1=row["tile_x1"],
                y0=row["tile_y0"],
                y1=row["tile_y1"],
                count=row["count"],
            )
            for row in cursor
        ]
    
    def get_tile_combinations(
        self,
        tile_x0: int,
        tile_y0: int,
        dilation: int,
        hierarchy_level: int,
        limit: int = 20,
    ) -> list[CombinationData]:
        # combinations present in a specific tile.
        if not self.is_loaded:
            return []
        
        cursor = self._conn.execute('''
            SELECT c.channels, t.count
            FROM combinations c
            JOIN tiles t ON c.id = t.combination_id
            WHERE t.tile_x0 = ? AND t.tile_y0 = ?
              AND c.dilation = ? AND c.hierarchy_level = ?
            ORDER BY t.count DESC
            LIMIT ?
        ''', (tile_x0, tile_y0, dilation, hierarchy_level, limit))
        
        return [
            CombinationData(
                channels=row["channels"].split("|") if row["channels"] else [],
                total_count=row["count"],
            )
            for row in cursor
        ]
    
    def get_dilation_curve(
        self,
        channels: list[str],
        hierarchy_level: int,
    ) -> list[dict]:
        # overlap counts across all dilations for a combination.
        if not self.is_loaded:
            return []
        
        channels_str = "|".join(sorted(channels))
        
        cursor = self._conn.execute('''
            SELECT dilation, total_count
            FROM combinations
            WHERE channels = ? AND hierarchy_level = ?
            ORDER BY dilation
        ''', (channels_str, hierarchy_level))
        
        return [
            {"dilation": row["dilation"], "count": row["total_count"]}
            for row in cursor
        ]
    
    def close(self):
        # close db
        if self._conn:
            self._conn.close()
            self._conn = None
        
        if self._db_path and self._db_path.exists():
            try:
                self._db_path.unlink()
            except Exception:
                pass
        
        self._loaded = False
        self.metadata = None
    
    def __del__(self):
        self.close()