from __future__ import annotations

import gzip
import json
import sqlite3
import tempfile
from dataclasses import dataclass, field
from itertools import combinations as iter_combinations
from pathlib import Path
from typing import Optional


@dataclass
class AnalysisMetadata:
    channels: list[str]
    hierarchy_levels: list[dict]
    dilation_amounts: list[float]
    volume_bounds: dict
    dtype_max: int = 65535  # max intensity value for dataset dtype (default: uint16)


@dataclass
class TileData:
    """Spatial tile with overlap/voxel data."""
    x0: int
    x1: int
    y0: int
    y1: int
    count: int  # raw voxel/intersection count
    active_fraction: float = 0.0  # fraction of tile volume occupied


@dataclass
class CombinationData:
    """A biomarker combination with its aggregated overlap data."""
    channels: list[str]
    total_count: int  # aggregated intersection count across all tiles
    iou: float = 0.0  # aggregated IoU (sum_inter / sum_union)
    overlap_coeff: float = 0.0
    tiles: list[TileData] = field(default_factory=list)


class AnalysisLoader:
    """
    Loader and query interface for .bioset analysis files (new schema).
    
    The new schema stores per-tile rows in `combinations` (1:1 with `tiles`),
    plus a `channel_stats` table for single-channel per-tile statistics.
    
    Usage:
        loader = AnalysisLoader()
        loader.load("/path/to/analysis.bioset")
        
        # Get metadata
        print(loader.metadata.channels)
        print(loader.metadata.dilation_amounts)
        
        # Get top combinations by aggregated IoU
        top_combos = loader.get_top_combinations(dilation=2.0, hierarchy_level=0, limit=50)
        
        # Get coverage percentages per channel
        coverage = loader.get_channel_coverage(dilation=0.0, hierarchy_level=0)
        
        # Get tiles with active fraction for heatmaps
        tiles = loader.get_combination_tiles(["CD8", "MART1"], dilation=2.0, hierarchy_level=0)
    """
    
    TILE_SIZES = {0: 128, 1: 256, 2: 512, 3: 1024}
    
    def __init__(self):
        self._conn: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None
        self._temp_dir: Optional[str] = None
        self.metadata: Optional[AnalysisMetadata] = None
        self._loaded = False
        self._total_tiles_cache: dict[int, int] = {}  # level -> total tile count
        self._channel_totals_voxels_cache: dict[
            tuple[str, float, int], int] = {}  # (channel, dilation, level) -> total voxels
    
    @property
    def is_loaded(self) -> bool:
        return self._loaded and self._conn is not None

    @property
    def db_path(self) -> Optional[Path]:
        """Path to the decompressed SQLite file on disk."""
        return self._db_path
    
    def _open_and_load_metadata(self):
        """Open the decompressed DB and load metadata."""
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        
        cursor = self._conn.execute("SELECT key, value FROM metadata")
        meta_dict = {row["key"]: json.loads(row["value"]) for row in cursor}
        
        self.metadata = AnalysisMetadata(
            channels=meta_dict.get("channels", []),
            hierarchy_levels=meta_dict.get("hierarchy_levels", []),
            dilation_amounts=meta_dict.get("dilation_amounts", []),
            volume_bounds=meta_dict.get("volume_bounds", {}),
            dtype_max=int(meta_dict.get("dtype_max", 65535)),
        )
        
        self._loaded = True
        self._total_tiles_cache.clear()
        
        print(f"[analysis] Loaded: {len(self.metadata.channels)} channels, "
              f"{len(self.metadata.dilation_amounts)} dilations, "
              f"{len(self.metadata.hierarchy_levels)} hierarchy levels")
        
        return self.metadata
    
    def load(self, file_path: str) -> AnalysisMetadata:
        """
        Load a .bioset analysis file.
        
        Args:
            file_path: Path to the .bioset file (gzipped SQLite)
            
        Returns:
            AnalysisMetadata with channels, hierarchy levels, etc.
        """
        self.close()
        
        self._temp_dir = tempfile.mkdtemp(prefix="bioset_analysis_")
        self._db_path = Path(self._temp_dir) / "analysis.db"
        
        print(f"[analysis] Loading {file_path}...")
        
        with gzip.open(file_path, 'rb') as f_in:
            with open(self._db_path, 'wb') as f_out:
                f_out.write(f_in.read())
        
        return self._open_and_load_metadata()
    
    def load_from_bytes(self, data: bytes) -> AnalysisMetadata:
        """
        Load from raw bytes (for file upload handling).
        
        Args:
            data: Raw bytes of the .bioset file
            
        Returns:
            AnalysisMetadata
        """
        self.close()
        
        self._temp_dir = tempfile.mkdtemp(prefix="bioset_analysis_")
        compressed_path = Path(self._temp_dir) / "uploaded.bioset"
        self._db_path = Path(self._temp_dir) / "analysis.db"
        
        with open(compressed_path, 'wb') as f:
            f.write(data)
        
        print(f"[analysis] Loading from uploaded bytes ({len(data)} bytes)...")
        
        with gzip.open(compressed_path, 'rb') as f_in:
            with open(self._db_path, 'wb') as f_out:
                f_out.write(f_in.read())
        
        return self._open_and_load_metadata()
    
    # ──────────────────────────────────────────────
    # Tile geometry helpers
    # ──────────────────────────────────────────────
    
    def get_tile_size(self, level: int) -> int:
        """Get tile edge length in voxels for a hierarchy level."""
        return self.TILE_SIZES.get(level, 128)
    
    def _get_z_depth(self) -> int:
        """Get Z depth in voxels from volume_bounds."""
        if not self.metadata or not self.metadata.volume_bounds:
            return 1
        z_bounds = self.metadata.volume_bounds.get("z", [0, 1])
        return max(1, z_bounds[1] - z_bounds[0])
    
    def _tile_volume(self, level: int, tile_x_span: int = 1, tile_y_span: int = 1) -> int:
        """Total voxels in a tile: tile_width * tile_height * z_depth."""
        ts = self.get_tile_size(level)
        return (tile_x_span * ts) * (tile_y_span * ts) * self._get_z_depth()
    
    def _get_total_tiles(self, hierarchy_level: int, dilation: float = 0.0) -> int:
        """
        Get the total number of distinct tiles at a hierarchy level.
        Uses channel_stats as the canonical source (always has data at dilation=0.0).
        """
        cache_key = hierarchy_level
        if cache_key in self._total_tiles_cache:
            return self._total_tiles_cache[cache_key]

        total = 0
        try:
            # Try channel_stats first (most reliable — every tile should appear)
            cursor = self._conn.execute('''
                SELECT COUNT(DISTINCT tile_x0 || ',' || tile_y0) as n
                FROM channel_stats
                WHERE hierarchy_level = ?
            ''', (hierarchy_level,))
            row = cursor.fetchone()
            total = row["n"] if row and row["n"] else 0
        except sqlite3.OperationalError as e:
            if "no such table: channel_stats" not in str(e):
                raise
        
        if total == 0:
            # Fallback: count from combinations/tiles
            cursor = self._conn.execute('''
                SELECT COUNT(DISTINCT t.tile_x0 || ',' || t.tile_y0) as n
                FROM combinations c
                JOIN tiles t ON c.id = t.combination_id
                WHERE c.hierarchy_level = ?
            ''', (hierarchy_level,))
            row = cursor.fetchone()
            total = row["n"] if row and row["n"] else 0
        
        self._total_tiles_cache[cache_key] = total
        return total
    
    # ──────────────────────────────────────────────
    # UpSet plot: aggregated IoU across tiles
    # ──────────────────────────────────────────────
    
    def get_top_combinations(
        self,
        dilation: float,
        hierarchy_level: int,
        limit: int = 50,
        min_channels: int = 2,
    ) -> list[CombinationData]:
        """
        Get top N combinations by aggregated IoU.
        
        Aggregates across all tiles:
        global_iou = SUM(total_count) / SUM(total_union)
        global_overlap_coeff = SUM(total_count) / MIN(SUM(ch_a), SUM(ch_b), ...)
        
        Sorted by global_iou DESC.
        Self-pairs (e.g. CD8|CD8) are excluded.
        """
        if not self.is_loaded:
            return []
        
        cursor = self._conn.execute('''
            SELECT 
                channels,
                SUM(total_count) as sum_inter,
                SUM(total_union) as sum_union,
                SUM(total_count) as agg_count
            FROM combinations
            WHERE dilation = ? AND hierarchy_level = ? AND channel_count >= ?
            GROUP BY channels
            HAVING sum_union > 0
            ORDER BY CAST(SUM(total_count) AS REAL) / SUM(total_union) DESC
            LIMIT ?
        ''', (dilation, hierarchy_level, min_channels, limit * 2))
        # fetch extra to account for self-pair filtering
        
        results = []
        for row in cursor:
            channels_str = row["channels"]
            channels = channels_str.split("|") if channels_str else []
            
            # Skip self-pairs
            if len(channels) != len(set(channels)):
                continue
            
            sum_inter = row["sum_inter"] or 0
            sum_union = row["sum_union"] or 1
            agg_iou = sum_inter / sum_union if sum_union > 0 else 0.0
            
            # Calculate overlap coefficient from channel_stats
            overlap_coeff = self._compute_overlap_coeff(
                channels, sum_inter, dilation, hierarchy_level
            )
            
            results.append(CombinationData(
                channels=channels,
                total_count=row["agg_count"] or 0,
                iou=agg_iou,
                overlap_coeff=overlap_coeff,
            ))
            
            if len(results) >= limit:
                break
        
        return results
    
    def get_filtered_combinations(
        self,
        channel_filter: list[str],
        dilation: float,
        hierarchy_level: int,
        limit: int = 50,
        exact_match: bool = False,
    ) -> list[CombinationData]:
        """
        Get combinations containing ANY of the specified channels, aggregated by IoU.
        Also computes aggregated overlap coefficient.
        """
        if not self.is_loaded or not channel_filter:
            return []
        
        if exact_match:
            channels_str = "|".join(sorted(
                channel_filter,
                key=lambda c: self.metadata.channels.index(c) if c in self.metadata.channels else 999
            ))
            cursor = self._conn.execute('''
                SELECT 
                    channels,
                    SUM(total_count) as sum_inter,
                    SUM(total_union) as sum_union,
                    SUM(total_count) as agg_count
                FROM combinations
                WHERE channels = ? AND dilation = ? AND hierarchy_level = ?
                GROUP BY channels
                HAVING sum_union > 0
            ''', (channels_str, dilation, hierarchy_level))
        else:
            query = '''
                SELECT 
                    channels,
                    SUM(total_count) as sum_inter,
                    SUM(total_union) as sum_union,
                    SUM(total_count) as agg_count
                FROM combinations
                WHERE dilation = ? AND hierarchy_level = ? AND channel_count >= 2
            '''
            params = [dilation, hierarchy_level]
            
            # OR logic: combination must contain ANY of the filter channels
            channel_clauses = []
            for ch in channel_filter:
                channel_clauses.append(
                    "(channels = ? OR channels LIKE ? OR channels LIKE ? OR channels LIKE ?)"
                )
                params.extend([ch, f"{ch}|%", f"%|{ch}", f"%|{ch}|%"])
            
            query += " AND (" + " OR ".join(channel_clauses) + ")"
            
            query += " GROUP BY channels HAVING sum_union > 0"
            query += " ORDER BY CAST(SUM(total_count) AS REAL) / SUM(total_union) DESC LIMIT ?"
            params.append(limit * 2)
            
            cursor = self._conn.execute(query, params)
        
        results = []
        for row in cursor:
            channels_str = row["channels"]
            channels = channels_str.split("|") if channels_str else []
            
            # Skip self-pairs
            if len(channels) != len(set(channels)):
                continue
            
            sum_inter = row["sum_inter"] or 0
            sum_union = row["sum_union"] or 1
            agg_iou = sum_inter / sum_union if sum_union > 0 else 0.0
            
            # Calculate overlap coefficient from channel_stats
            overlap_coeff = self._compute_overlap_coeff(
                channels, sum_inter, dilation, hierarchy_level
            )
            
            results.append(CombinationData(
                channels=channels,
                total_count=row["agg_count"] or 0,
                iou=agg_iou,
                overlap_coeff=overlap_coeff,
            ))
            
            if len(results) >= limit:
                break
        
        return results
    
    def _compute_overlap_coeff(
        self,
        channels: list[str],
        sum_inter: int,
        dilation: float,
        hierarchy_level: int,
    ) -> float:
        """
        Compute aggregated overlap coefficient.
        
        overlap_coeff = SUM(intersection) / MIN(SUM(ch_a), SUM(ch_b), ...)
        
        Queries channel_stats to get total voxels per channel.
        """
        if not channels or sum_inter == 0:
            return 0.0
        
        # Get total voxels for each channel
        channel_totals = []
        for ch in channels:
            total = self.get_channel_total_voxels(ch, dilation, hierarchy_level)
            if total > 0:
                channel_totals.append(total)
        
        if not channel_totals:
            return 0.0
        
        min_voxels = min(channel_totals)
        return sum_inter / min_voxels if min_voxels > 0 else 0.0


    
    # ──────────────────────────────────────────────
    # Bar chart: coverage percentage
    # ──────────────────────────────────────────────
    
    def get_channel_coverage(
        self,
        dilation: float,
        hierarchy_level: int,
    ) -> list[tuple[str, float]]:
        """
        Voxel density = SUM(voxel_count) / total_volume × 100
        
        This is consistent across hierarchy levels.
        """
        if not self.is_loaded:
            return []
        
        # Get total volume (same regardless of hierarchy level)
        bounds = self.metadata.volume_bounds
        total_volume = (
            (bounds["x"][1] - bounds["x"][0]) *
            (bounds["y"][1] - bounds["y"][0]) *
            (bounds["z"][1] - bounds["z"][0])
        )
        
        if total_volume == 0:
            return []

        try:
            cursor = self._conn.execute('''
                SELECT 
                    channel,
                    SUM(voxel_count) as total_voxels
                FROM channel_stats
                WHERE dilation = ? AND hierarchy_level = ?
                GROUP BY channel
                ORDER BY total_voxels DESC
            ''', (dilation, hierarchy_level))
            rows = cursor.fetchall()
            # Fallback to dilation=0.0
            if not rows and dilation != 0.0:
                cursor = self._conn.execute('''
                    SELECT 
                        channel,
                        SUM(voxel_count) as total_voxels
                    FROM channel_stats
                    WHERE dilation = 0.0 AND hierarchy_level = ?
                    GROUP BY channel
                    ORDER BY total_voxels DESC
                ''', (hierarchy_level,))
                rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if "no such table: channel_stats" in str(e):
                return []
            raise
        
        results = []
        for row in rows:
            density_pct = (row["total_voxels"] / total_volume) * 100.0
            results.append((row["channel"], density_pct))
        
        return results
    
    # ──────────────────────────────────────────────
    # Heatmap: tiles with active fraction
    # ──────────────────────────────────────────────
    
    def get_combination_tiles(
        self,
        channels: list[str],
        dilation: float,
        hierarchy_level: int,
    ) -> list[TileData]:
        """
        Get tiles for a specific channel or combination with active fraction.
        
        For a single channel, queries `channel_stats`.
        For multi-channel combinations, queries `combinations` JOIN `tiles`.
        
        active_fraction = voxel_count / tile_volume (for single channel)
                        = inter_count / tile_volume (for combinations)
        """
        if not self.is_loaded:
            return []
        
        tile_size = self.get_tile_size(hierarchy_level)
        z_depth = self._get_z_depth()
        
        if len(channels) == 1:
            return self._get_single_channel_tiles(
                channels[0], dilation, hierarchy_level, tile_size, z_depth
            )
        else:
            return self._get_multi_channel_tiles(
                channels, dilation, hierarchy_level, tile_size, z_depth
            )
    
    def _get_single_channel_tiles(
        self,
        channel: str,
        dilation: float,
        hierarchy_level: int,
        tile_size: int,
        z_depth: int,
    ) -> list[TileData]:
        """Get tiles from channel_stats for a single channel."""
        try:
            cursor = self._conn.execute('''
                SELECT tile_x0, tile_x1, tile_y0, tile_y1, voxel_count
                FROM channel_stats
                WHERE channel = ? AND dilation = ? AND hierarchy_level = ?
                ORDER BY voxel_count DESC
            ''', (channel, dilation, hierarchy_level))
            rows = cursor.fetchall()
            # Fallback to dilation=0.0
            if not rows and dilation != 0.0:
                cursor = self._conn.execute('''
                    SELECT tile_x0, tile_x1, tile_y0, tile_y1, voxel_count
                    FROM channel_stats
                    WHERE channel = ? AND dilation = 0.0 AND hierarchy_level = ?
                    ORDER BY voxel_count DESC
                ''', (channel, hierarchy_level))
                rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if "no such table: channel_stats" in str(e):
                return []
            raise
        
        results = []
        for row in rows:
            x_span = max(1, row["tile_x1"] - row["tile_x0"])
            y_span = max(1, row["tile_y1"] - row["tile_y0"])
            tile_vol = (x_span * tile_size) * (y_span * tile_size) * z_depth
            voxel_count = row["voxel_count"] or 0
            active_frac = voxel_count / tile_vol if tile_vol > 0 else 0.0
            
            results.append(TileData(
                x0=row["tile_x0"],
                x1=row["tile_x1"],
                y0=row["tile_y0"],
                y1=row["tile_y1"],
                count=voxel_count,
                active_fraction=active_frac,
            ))
        
        return results
    
    def _get_multi_channel_tiles(
        self,
        channels: list[str],
        dilation: float,
        hierarchy_level: int,
        tile_size: int,
        z_depth: int,
    ) -> list[TileData]:
        """Get tiles from combinations+tiles for multi-channel overlaps."""
        # Sort channels by metadata index order
        channel_order = self.metadata.channels if self.metadata else []
        sorted_channels = sorted(
            channels,
            key=lambda c: channel_order.index(c) if c in channel_order else 999
        )
        channels_str = "|".join(sorted_channels)
        
        cursor = self._conn.execute('''
            SELECT t.tile_x0, t.tile_x1, t.tile_y0, t.tile_y1, t.inter_count
            FROM combinations c
            JOIN tiles t ON c.id = t.combination_id
            WHERE c.channels = ? AND c.dilation = ? AND c.hierarchy_level = ?
            ORDER BY t.inter_count DESC
        ''', (channels_str, dilation, hierarchy_level))
        
        results = []
        for row in cursor:
            x_span = max(1, row["tile_x1"] - row["tile_x0"])
            y_span = max(1, row["tile_y1"] - row["tile_y0"])
            tile_vol = (x_span * tile_size) * (y_span * tile_size) * z_depth
            inter_count = row["inter_count"] or 0
            active_frac = inter_count / tile_vol if tile_vol > 0 else 0.0
            
            results.append(TileData(
                x0=row["tile_x0"],
                x1=row["tile_x1"],
                y0=row["tile_y0"],
                y1=row["tile_y1"],
                count=inter_count,
                active_fraction=active_frac,
            ))
        
        return results
    
    # ──────────────────────────────────────────────
    # Tile-level queries (for local plots / drill-down)
    # ──────────────────────────────────────────────
    
    def get_tile_combinations(
        self,
        tile_x0: int,
        tile_y0: int,
        dilation: float,
        hierarchy_level: int,
        limit: int = 20,
    ) -> list[CombinationData]:
        """Get combinations present in a specific tile."""
        if not self.is_loaded:
            return []
        
        cursor = self._conn.execute('''
            SELECT c.channels, c.iou, c.overlap_coeff, t.inter_count
            FROM combinations c
            JOIN tiles t ON c.id = t.combination_id
            WHERE t.tile_x0 = ? AND t.tile_y0 = ?
              AND c.dilation = ? AND c.hierarchy_level = ?
              AND c.channel_count >= 2
            ORDER BY c.iou DESC
            LIMIT ?
        ''', (tile_x0, tile_y0, dilation, hierarchy_level, limit))
        
        results = []
        for row in cursor:
            channels = row["channels"].split("|") if row["channels"] else []
            if len(channels) != len(set(channels)):
                continue
            results.append(CombinationData(
                channels=channels,
                total_count=row["inter_count"] or 0,
                iou=row["iou"] or 0.0,
                overlap_coeff=row["overlap_coeff"] or 0.0,
            ))
        
        return results
    
    # ──────────────────────────────────────────────
    # Dilation curve
    # ──────────────────────────────────────────────
    
    def get_subcombination_dilation_curves(
        self,
        channels: list[str],
        hierarchy_level: int = 0,
    ) -> dict[str, list[dict]]:
        """
        Get dilation curves for all subcombinations of the given channels.
        
        For channels ["A", "B", "C"], attempts to find curves for:
        - Single channels: A, B, C
        - Pairs: A|B, A|C, B|C (if they exist in the database)
        - Triple: A|B|C (if it exists)
        
        Only returns subcombinations that actually exist in the database.
        
        Returns:
            Dict mapping channel string (e.g., "A|B") to list of dilation points:
            {
                "A": [{"dilation": 0.0, "count": ..., "iou": 1.0, "overlap_coeff": 1.0, "density": ...}, ...],
                "A|B": [{"dilation": 0.0, "count": ..., "iou": ..., "overlap_coeff": ..., "density": ...}, ...],
                ...
            }
            
        Each dilation point dict contains:
            - dilation: float
            - count: int (voxel count or intersection count)
            - iou: float
            - overlap_coeff: float
            - density: float (percentage of total volume)
        """
        if not self.is_loaded or not channels:
            return {}
        
        # Get channel ordering for consistent key generation
        channel_order = self.metadata.channels if self.metadata else []
        
        def sort_channels(ch_list: list[str]) -> list[str]:
            return sorted(
                ch_list,
                key=lambda c: channel_order.index(c) if c in channel_order else 999
            )
        
        def make_key(ch_list: list[str]) -> str:
            return "|".join(sort_channels(ch_list))
        
        results = {}
        
        # Generate all subcombinations of size 1 to len(channels)
        for size in range(1, len(channels) + 1):
            for combo in iter_combinations(channels, size):
                combo_list = list(combo)
                combo_key = make_key(combo_list)
                
                # Get dilation curve for this subcombination
                if size == 1:
                    curve = self._get_single_channel_dilation_curve(
                        combo_list[0], hierarchy_level
                    )
                else:
                    curve = self._get_multi_channel_dilation_curve_full(
                        combo_list, hierarchy_level
                    )
                
                # Only include if data exists
                if curve:
                    results[combo_key] = curve
        
        return results
    
    def _get_single_channel_dilation_curve(
        self,
        channel: str,
        hierarchy_level: int,
    ) -> list[dict]:
        """Get voxel count across dilations for a single channel."""
        
        # Get total volume for density calculation
        bounds = self.metadata.volume_bounds
        total_volume = (
            (bounds["x"][1] - bounds["x"][0]) *
            (bounds["y"][1] - bounds["y"][0]) *
            (bounds["z"][1] - bounds["z"][0])
        )
        
        try:
            cursor = self._conn.execute('''
                SELECT 
                    dilation,
                    SUM(voxel_count) as total_voxels
                FROM channel_stats
                WHERE channel = ? AND hierarchy_level = ?
                GROUP BY dilation
                ORDER BY dilation
            ''', (channel, hierarchy_level))
            
            results = []
            for row in cursor:
                total_voxels = row["total_voxels"] or 0
                density = (total_voxels / total_volume * 100) if total_volume > 0 else 0.0
                
                results.append({
                    "dilation": row["dilation"],
                    "count": total_voxels,
                    "iou": 1.0,  # Self-overlap is always 1.0
                    "overlap_coeff": 1.0,  # Self-overlap is always 1.0
                    "density": density,
                })
            
            return results
            
        except sqlite3.OperationalError as e:
            if "no such table: channel_stats" in str(e):
                return []
            raise


    def _get_multi_channel_dilation_curve_full(
            self,
            channels: list[str],
            hierarchy_level: int,
        ) -> list[dict]:
            """
            Get IoU, overlap coefficient, and density across dilations for a multi-channel combination.
            
            Returns empty list if the combination doesn't exist in the database.
            """
            # Get total volume for density calculation
            bounds = self.metadata.volume_bounds
            total_volume = (
                (bounds["x"][1] - bounds["x"][0]) *
                (bounds["y"][1] - bounds["y"][0]) *
                (bounds["z"][1] - bounds["z"][0])
            )
            
            channel_order = self.metadata.channels if self.metadata else []
            sorted_channels = sorted(
                channels,
                key=lambda c: channel_order.index(c) if c in channel_order else 999
            )
            channels_str = "|".join(sorted_channels)
            
            cursor = self._conn.execute('''
                SELECT 
                    dilation,
                    SUM(total_count) as sum_inter,
                    SUM(total_union) as sum_union
                FROM combinations
                WHERE channels = ? AND hierarchy_level = ?
                GROUP BY dilation
                ORDER BY dilation
            ''', (channels_str, hierarchy_level))
            
            rows = cursor.fetchall()
            
            if not rows:
                return []  # Combination doesn't exist in database
            
            results = []
            for row in rows:
                dilation = row["dilation"]
                sum_inter = row["sum_inter"] or 0
                sum_union = row["sum_union"] or 1
                
                # IoU
                iou = sum_inter / sum_union if sum_union > 0 else 0.0
                
                # Overlap coefficient: intersection / min(channel_voxels)
                overlap_coeff = self._compute_overlap_coeff(
                    channels, sum_inter, dilation, hierarchy_level
                )
                
                # Density of intersection
                density = (sum_inter / total_volume * 100) if total_volume > 0 else 0.0
                
                results.append({
                    "dilation": dilation,
                    "count": sum_inter,
                    "iou": iou,
                    "overlap_coeff": overlap_coeff,
                    "density": density,
                })
            
            return results
    
    # ──────────────────────────────────────────────
    # Channel voxel totals (for reference)
    # ──────────────────────────────────────────────
    
    def get_channel_total_voxels(
        self, channel: str, dilation: float, level: int
    ) -> int:
        """Get total voxels for a single channel across all tiles."""
        if not self.is_loaded:
            return 0

        cache_entry = (channel, dilation, level)
        if cache_entry in self._channel_totals_voxels_cache:
            return self._channel_totals_voxels_cache[cache_entry]
            
        try:
            cursor = self._conn.execute('''
                SELECT SUM(voxel_count) as total
                FROM channel_stats
                WHERE channel = ? AND dilation = ? AND hierarchy_level = ?
            ''', (channel, dilation, level))
            row = cursor.fetchone()
            total = row["total"] if row and row["total"] else 0

            self._channel_totals_voxels_cache[cache_entry] = total
            return total
        except sqlite3.OperationalError as e:
            if "no such table: channel_stats" in str(e):
                return 0
            raise
    
    def get_tile_channel_stats(
        self, tile_x0: int, tile_y0: int, level: int, dilation: float
    ) -> list[dict]:
        """Return per-channel stats for a single tile at the given level and dilation.

        Returns a list of dicts with keys:
            channel, voxel_count, sum_intensity, mean_intensity
        sorted descending by voxel_count.
        """
        if not self.is_loaded:
            return []
        cursor = self._conn.execute('''
            SELECT channel, voxel_count, sum_intensity, mean_intensity
            FROM channel_stats
            WHERE tile_x0 = ? AND tile_y0 = ?
              AND hierarchy_level = ?
              AND dilation = ?
            ORDER BY voxel_count DESC
        ''', (tile_x0, tile_y0, level, dilation))
        return [dict(row) for row in cursor.fetchall()]

    # ──────────────────────────────────────────────
    # Viewport-local queries (metrics for selected tiles)
    # ──────────────────────────────────────────────

    @staticmethod
    def _tile_filter_sql(tile_coords: list[tuple[int, int]], table_prefix: str = "") -> tuple[str, list]:
        """Build SQL WHERE clause and params for filtering by (tile_x0, tile_y0) pairs.

        Returns (clause_str, params_list) where clause_str is like
        "(t.tile_x0 = ? AND t.tile_y0 = ?) OR (t.tile_x0 = ? AND t.tile_y0 = ?) ..."
        """
        prefix = f"{table_prefix}." if table_prefix else ""
        clauses = []
        params = []
        for x0, y0 in tile_coords:
            clauses.append(f"({prefix}tile_x0 = ? AND {prefix}tile_y0 = ?)")
            params.extend([x0, y0])
        return "(" + " OR ".join(clauses) + ")", params

    def get_viewport_metrics(
        self,
        tile_coords: list[tuple[int, int]],
        dilation: float,
        hierarchy_level: int,
        min_channels: int = 2,
        limit: int = 50,
    ) -> dict:
        """Compute channel voxels and combination IoU/overlap_coeff for selected tiles.

        Returns:
            {
                "channels": {channel_name: {"voxel_count": int, "density": float}, ...},
                "combinations": [{"channels": [...], "iou": float, "overlap_coeff": float, "sum_inter": int}, ...],
            }
        """
        if not self.is_loaded or not tile_coords:
            return {"channels": {}, "combinations": []}

        tile_filter, tile_params = self._tile_filter_sql(tile_coords)

        # 1. Per-channel voxel counts for selected tiles
        query_ch = f'''
            SELECT channel, SUM(voxel_count) as total_voxels
            FROM channel_stats
            WHERE dilation = ? AND hierarchy_level = ?
              AND {tile_filter}
            GROUP BY channel
        '''
        params_ch = [dilation, hierarchy_level] + tile_params
        cursor = self._conn.execute(query_ch, params_ch)
        channel_voxels = {row["channel"]: row["total_voxels"] or 0 for row in cursor}

        # Compute tile volume for density
        tile_size = self.get_tile_size(hierarchy_level)
        z_depth = self._get_z_depth()
        tile_volume = len(tile_coords) * tile_size * tile_size * z_depth

        channel_data = {}
        for ch, voxels in channel_voxels.items():
            density = (voxels / tile_volume * 100) if tile_volume > 0 else 0.0
            channel_data[ch] = {"voxel_count": voxels, "density": density}

        # 2. Combination metrics using tiles.union_count for exact IoU
        tile_filter_t, tile_params_t = self._tile_filter_sql(tile_coords, table_prefix="t")
        query_combo = f'''
            SELECT c.channels, c.channel_count,
                   SUM(t.inter_count) as sum_inter,
                   SUM(t.union_count) as sum_union
            FROM combinations c
            JOIN tiles t ON c.id = t.combination_id
            WHERE c.dilation = ? AND c.hierarchy_level = ? AND c.channel_count >= ?
              AND {tile_filter_t}
            GROUP BY c.channels
            HAVING sum_union > 0
            ORDER BY CAST(SUM(t.inter_count) AS REAL) / SUM(t.union_count) DESC
            LIMIT ?
        '''
        params_combo = [dilation, hierarchy_level, min_channels] + tile_params_t + [limit * 2]
        cursor = self._conn.execute(query_combo, params_combo)

        combinations = []
        for row in cursor:
            channels_str = row["channels"]
            channels = channels_str.split("|") if channels_str else []
            if len(channels) != len(set(channels)):
                continue

            sum_inter = row["sum_inter"] or 0
            sum_union = row["sum_union"] or 1
            iou = sum_inter / sum_union if sum_union > 0 else 0.0

            # Overlap coefficient from viewport channel voxels
            ch_totals = [channel_voxels.get(ch, 0) for ch in channels]
            min_voxels = min(ch_totals) if ch_totals else 0
            overlap_coeff = sum_inter / min_voxels if min_voxels > 0 else 0.0

            combinations.append({
                "channels": channels,
                "iou": iou,
                "overlap_coeff": overlap_coeff,
                "sum_inter": sum_inter,
            })
            if len(combinations) >= limit:
                break

        return {"channels": channel_data, "combinations": combinations}

    def get_viewport_dilation_curves(
        self,
        tile_coords: list[tuple[int, int]],
        channels: list[str],
        hierarchy_level: int = 0,
    ) -> dict[str, list[dict]]:
        """Get dilation curves for subcombinations of channels, restricted to selected tiles.

        Returns same format as get_subcombination_dilation_curves().
        """
        if not self.is_loaded or not tile_coords or not channels:
            return {}

        channel_order = self.metadata.channels if self.metadata else []

        def sort_channels(ch_list):
            return sorted(ch_list, key=lambda c: channel_order.index(c) if c in channel_order else 999)

        def make_key(ch_list):
            return "|".join(sort_channels(ch_list))

        tile_filter, tile_params = self._tile_filter_sql(tile_coords)
        tile_filter_t, tile_params_t = self._tile_filter_sql(tile_coords, table_prefix="t")

        # Tile volume for density
        tile_size = self.get_tile_size(hierarchy_level)
        z_depth = self._get_z_depth()
        tile_volume = len(tile_coords) * tile_size * tile_size * z_depth

        results = {}
        for size in range(1, len(channels) + 1):
            for combo in iter_combinations(channels, size):
                combo_list = list(combo)
                combo_key = make_key(combo_list)

                if size == 1:
                    curve = self._viewport_single_channel_curve(
                        combo_list[0], tile_filter, tile_params, hierarchy_level, tile_volume
                    )
                else:
                    curve = self._viewport_multi_channel_curve(
                        combo_list, tile_filter, tile_params, tile_filter_t, tile_params_t,
                        hierarchy_level, tile_volume
                    )

                if curve:
                    results[combo_key] = curve

        return results

    def _viewport_single_channel_curve(
        self, channel, tile_filter, tile_params, hierarchy_level, tile_volume
    ) -> list[dict]:
        """Dilation curve for a single channel restricted to viewport tiles."""
        query = f'''
            SELECT dilation, SUM(voxel_count) as total_voxels
            FROM channel_stats
            WHERE channel = ? AND hierarchy_level = ?
              AND {tile_filter}
            GROUP BY dilation
            ORDER BY dilation
        '''
        params = [channel, hierarchy_level] + tile_params
        try:
            cursor = self._conn.execute(query, params)
        except sqlite3.OperationalError:
            return []

        results = []
        for row in cursor:
            total_voxels = row["total_voxels"] or 0
            density = (total_voxels / tile_volume * 100) if tile_volume > 0 else 0.0
            results.append({
                "dilation": row["dilation"],
                "count": total_voxels,
                "iou": 1.0,
                "overlap_coeff": 1.0,
                "density": density,
            })
        return results

    def _viewport_multi_channel_curve(
        self, channels, tile_filter, tile_params, tile_filter_t, tile_params_t,
        hierarchy_level, tile_volume
    ) -> list[dict]:
        """Dilation curve for a multi-channel combination restricted to viewport tiles."""
        channel_order = self.metadata.channels if self.metadata else []
        sorted_channels = sorted(
            channels, key=lambda c: channel_order.index(c) if c in channel_order else 999
        )
        channels_str = "|".join(sorted_channels)

        query = f'''
            SELECT c.dilation,
                   SUM(t.inter_count) as sum_inter,
                   SUM(t.union_count) as sum_union
            FROM combinations c
            JOIN tiles t ON c.id = t.combination_id
            WHERE c.channels = ? AND c.hierarchy_level = ?
              AND {tile_filter_t}
            GROUP BY c.dilation
            ORDER BY c.dilation
        '''
        params = [channels_str, hierarchy_level] + tile_params_t
        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            return []

        results = []
        for row in rows:
            dilation_val = row["dilation"]
            sum_inter = row["sum_inter"] or 0
            sum_union = row["sum_union"] or 1
            iou = sum_inter / sum_union if sum_union > 0 else 0.0

            # Overlap coefficient: need per-channel voxels at this dilation for viewport tiles
            ch_totals = []
            for ch in channels:
                ch_query = f'''
                    SELECT SUM(voxel_count) as total
                    FROM channel_stats
                    WHERE channel = ? AND dilation = ? AND hierarchy_level = ?
                      AND {tile_filter}
                '''
                ch_params = [ch, dilation_val, hierarchy_level] + tile_params
                ch_cursor = self._conn.execute(ch_query, ch_params)
                ch_row = ch_cursor.fetchone()
                ch_totals.append(ch_row["total"] or 0 if ch_row else 0)

            min_voxels = min(ch_totals) if ch_totals else 0
            overlap_coeff = sum_inter / min_voxels if min_voxels > 0 else 0.0
            density = (sum_inter / tile_volume * 100) if tile_volume > 0 else 0.0

            results.append({
                "dilation": dilation_val,
                "count": sum_inter,
                "iou": iou,
                "overlap_coeff": overlap_coeff,
                "density": density,
            })

        return results

    # ──────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────

    def close(self):
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
        self._total_tiles_cache.clear()
        self._channel_totals_voxels_cache.clear()
    
    def __del__(self):
        self.close()
