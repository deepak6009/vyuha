import os
import cv2
import time
import json
import numpy as np
import dask.array as da
from .wsi_dask import wsi_da
from scipy.stats import norm
import gc
from .visitcheck import Visited

class ProcessCollection:

    def __init__(self, queue, motion_current=None,
                 camera_width=None, camera_height=None,
                 pixel_size_um=None, enable_blending=False):
        self.queue = queue
        self.wsi_dask = wsi_da()
        self.secondary_image = None
        # Load parameters first to populate total_cols, total_rows, etc.
        self._load_parameters(motion_current,
                              camera_width=camera_width,
                              camera_height=camera_height,
                              pixel_size_um=pixel_size_um,
                              enable_blending=enable_blending)
        self.total_frames = self.total_cols * self.total_rows
        
        self.visit = Visited(self.total_cols, self.total_rows)
        # Calculate crops using the parameters loaded above.
        self._calculate_dynamic_crops()
        self.row_offsets = {}
        self.row_vertical_offset = 0
        self.orb = cv2.ORB_create(nfeatures=2000, scaleFactor=1.2, nlevels=8)
        self.akaze = cv2.AKAZE_create()
        self.MIN_MATCHES = 10
        self.scale = 2  # upscale factor
        self._bad_tiles = set()
        self.DEBUG = True         # set False to silence
        self.DEBUG_MAX = 5000     # stop after N debug lines
        self._dbg_count = 0
        self.debug_vertical_all_cols = False   # Strategy B
        self.vertical_single_anchor = True     # Strategy A (only used when debug flag is False)
        self.blend_power = 1.0
        self.removed_count = 0  # Support relative indexing for popping queues

    def blend_hstack(self, left, right, blend_width):
        if blend_width <= 0:
            return np.hstack((left, right))
        h = max(left.shape[0], right.shape[0])
        canvas_w = left.shape[1] + right.shape[1] - blend_width
        canvas = np.full((h, canvas_w, 3), 255, dtype=np.uint8)
        # Pad shorter tiles with white
        if left.shape[0] < h:
            left = np.vstack((left, np.full((h - left.shape[0], left.shape[1], 3), 255, dtype=np.uint8)))
        if right.shape[0] < h:
            right = np.vstack((right, np.full((h - right.shape[0], right.shape[1], 3), 255, dtype=np.uint8)))
        # Place left fully
        canvas[:, :left.shape[1]] = left
        # Place right non-overlap part
        non_overlap_right = right[:, blend_width:]
        paste_x = left.shape[1]
        canvas[:, paste_x:paste_x + non_overlap_right.shape[1]] = non_overlap_right
        # Strong fade-in only for right tile in overlap
        overlap_left = left[:, -blend_width:]
        overlap_right = right[:, :blend_width]
        # Very steep fade-in curve for target (right/bottom)
        position = np.linspace(0, 1, blend_width)
        alpha = position ** self.blend_power
        alpha = alpha.reshape(1, blend_width, 1)
        blended_overlap = (overlap_left * (1 - alpha) + overlap_right * alpha).astype(np.uint8)
        canvas[:, left.shape[1] - blend_width:left.shape[1]] = blended_overlap
        return canvas

    def blend_vstack(self, top, bottom, blend_width):
        if blend_width <= 0:
            return np.vstack((top, bottom))
        w = max(top.shape[1], bottom.shape[1])
        canvas_h = top.shape[0] + bottom.shape[0] - blend_width
        canvas = np.full((canvas_h, w, 3), 255, dtype=np.uint8)
        # Pad narrower tiles
        if top.shape[1] < w:
            top = np.hstack((top, np.full((top.shape[0], w - top.shape[1], 3), 255, dtype=np.uint8)))
        if bottom.shape[1] < w:
            bottom = np.hstack((bottom, np.full((bottom.shape[0], w - bottom.shape[1], 3), 255, dtype=np.uint8)))
        # Place top fully
        canvas[:top.shape[0], :] = top
        # Place bottom non-overlap
        non_overlap_bottom = bottom[blend_width:, :]
        paste_y = top.shape[0]
        canvas[paste_y:paste_y + non_overlap_bottom.shape[0], :] = non_overlap_bottom
        # Strong fade-in for bottom tile
        overlap_top = top[-blend_width:, :]
        overlap_bottom = bottom[:blend_width, :]
        x = np.linspace(0, 1, blend_width)
        alpha = np.power(x, self.blend_power) # very steep → top stays visible longer
        alpha = alpha.reshape(-1, 1, 1)
        blended_overlap = (overlap_top * (1 - alpha) + overlap_bottom * alpha).astype(np.uint8)
        canvas[top.shape[0] - blend_width:top.shape[0], :] = blended_overlap
        return canvas

    def blend_hstack_dask(self, left_da, right_da, blend_width):
        left = left_da.compute()
        right = right_da.compute()
        blended = self.blend_hstack(left, right, blend_width)
        return self._as_dask_tile(blended)

    def blend_vstack_dask(self, top_da, bottom_da, blend_width):
        top = top_da.compute()
        bottom = bottom_da.compute()
        blended = self.blend_vstack(top, bottom, blend_width)
        return self._as_dask_tile(blended)

    def _calculate_dynamic_crops(self):
        """
        Calculates crop margins (actual_x, actual_y) and registration edges (edge_x, edge_y)
        dynamically based on camera optics and stage movement to ensure hardware-agnostic deployment.
        """
        # NOTE: For true flexibility, add these to spinnaker_parameters.json
        # instead of hardcoding them here.
        # 1. Use effective pixel size from load_parameters
        eff_um_per_px = self.effective_pixel_size_um
        self._dbg(f"[Optics] Effective pixel size: {eff_um_per_px:.4f} um/px")
        
        # 2. Use calculated overlaps and steps from load_parameters
        overlap_x = self.overlap_x_px
        overlap_y = self.overlap_y_px
        self._dbg(f"[Optics] Overlap: overlap_x={overlap_x:.1f}, overlap_y={overlap_y:.1f}")
        
        if overlap_x <= 0 or overlap_y <= 0:
            raise ValueError(f"No overlap: overlap_x={overlap_x:.1f}, overlap_y={overlap_y:.1f}. Check stage step vs FOV.")
        
        if self.enable_blending:
            bo_factor = 0.5
            self.blend_overlap_x = min(128, int(overlap_x * bo_factor))
            self.blend_overlap_y = min(128, int(overlap_y * bo_factor))
        else:
            self.blend_overlap_x = 0
            self.blend_overlap_y = 0
        
        # 4. Determine crop per side to leave blend_overlap in tiles
        self.actual_x = max(0, int((overlap_x - self.blend_overlap_x) / 2))
        self.actual_y = max(0, int((overlap_y - self.blend_overlap_y) / 2))
        
        self.nominal_step_x = int(self.step_x_px)
        self.nominal_step_y = int(self.step_y_px)
        
        # 5. Calculate registration edges (how deep to look for matches)
        #    Typically we look deep into the overlap minus a small safety margin.
        # margin_x = max(24, int(round(0.03 * overlap_x)))  # 3% or >=24px
        # margin_y = max(24, int(round(0.03 * overlap_y)))

        margin_x = 0
        margin_y = 0
        
        # edge_x/y is the depth from the boundary into which we search for features
        self.edge_x = int(max(64, min(overlap_x - margin_x, self.camera_full_width // 2)))
        self.edge_y = int(max(64, min(overlap_y - margin_y, self.camera_full_height // 2)))
        
        # 6. Calculate final cropped dimensions for the display mosaic
        self.width = self.camera_full_width - (2 * self.actual_x)
        self.height = self.camera_full_height - (2 * self.actual_y)
        
        # 7. Set dynamic centers
        center_x = self.camera_full_width // 2
        center_y = self.camera_full_height // 2
        self.prev_centerx = center_x
        self.prev_centery = center_y
        self.CANON_CENTER = (center_x, center_y)

        self._dbg(f"[Optics] Calculated Crops: actual_x={self.actual_x} actual_y={self.actual_y}")
        self._dbg(f"[Optics] Calculated Edges: edge_x={self.edge_x} edge_y={self.edge_y}")
        self._dbg(f"[Optics] Blend Overlaps: x={self.blend_overlap_x} y={self.blend_overlap_y}")
        self._dbg(f"[Optics] Nominal Steps: x={self.nominal_step_x} y={self.nominal_step_y}")
        self._dbg(f"[Optics] Final Tile Size: {self.width}x{self.height}")

    def _as_dask_tile(self, tile_np: np.ndarray) -> da.Array:
        """
        Convert a NumPy tile into a Dask array.
        One tile = one chunk (simple + stable).
        """
        if tile_np.dtype != np.uint8:
            tile_np = tile_np.astype(np.uint8)

        # chunk exactly one tile (H, W, C)
        return da.from_array(tile_np, chunks=tile_np.shape)

    def _dbg(self, msg: str):
        if not getattr(self, "DEBUG", False):
            return
        self._dbg_count = getattr(self, "_dbg_count", 0) + 1
        if self._dbg_count > getattr(self, "DEBUG_MAX", 10**9):
            return
        print(msg)

    def _load_parameters(self, motion_current=None,
                         camera_width=None, camera_height=None,
                         pixel_size_um=None, enable_blending=False):
        """Load stitching parameters from direct arguments or JSON fallback.

        Camera dimensions are taken from direct arguments when provided (live
        scan path) so no JSON file is required at runtime.  Grid dimensions are
        taken from ``motion_current`` when provided, otherwise from the local
        ``spinnaker_parameters.json`` (offline / test path only).

        Parameters
        ----------
        motion_current : object, optional
            Must expose ``x_steps``, ``y_steps``,
            ``target_overlap_percentage``.
        camera_width : int, optional
            Full frame width in pixels read from the live camera.
        camera_height : int, optional
            Full frame height in pixels read from the live camera.
        pixel_size_um : float, optional
            Effective pixel size in microns (OME-TIFF metadata only).
        enable_blending : bool, optional
            Whether to alpha-blend tile overlaps.
        """
        # ── 1. Camera sensor dimensions ───────────────────────────────────────
        if camera_width is not None and camera_height is not None:
            self.camera_full_width  = int(camera_width)
            self.camera_full_height = int(camera_height)
            self.effective_pixel_size_um = float(pixel_size_um) if pixel_size_um is not None else 1.0
            self.enable_blending = bool(enable_blending)
            print(f"[Config] Camera from live source: {self.camera_full_width}x{self.camera_full_height}")
        else:
            try:
                config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                           'spinnaker_parameters.json')
                with open(config_path, 'r') as f:
                    config = json.load(f)
                cam = config.get('camera', {})
                self.camera_full_width  = int(cam.get('width',  3280))
                self.camera_full_height = int(cam.get('height', 2464))
                self.effective_pixel_size_um = float(cam.get('effective_pixel_size_um', 1.0))
                stitch_cfg = config.get("stitch", {})
                self.enable_blending = stitch_cfg.get("enable_blending", "False").lower() == "true"
                print(f"[Config] Camera from JSON: {self.camera_full_width}x{self.camera_full_height}")
            except Exception as e:
                print(f"[Error] Failed to load camera params from JSON: {e}")
                raise

        # ── 2. Grid dimensions and overlap ────────────────────────────────────
        if motion_current is not None:
            self.total_cols                = int(motion_current.x_steps)
            self.total_rows                = int(motion_current.y_steps)
            self.target_overlap_percentage = float(motion_current.target_overlap_percentage)
            print(f"[Config] Grid from motion_current: {self.total_cols}x{self.total_rows} "
                  f"overlap={self.target_overlap_percentage}%")
        else:
            try:
                config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                           'spinnaker_parameters.json')
                with open(config_path, 'r') as f:
                    config = json.load(f)
                stitch_cfg = config.get("stitch", {})
                self.target_overlap_percentage = float(stitch_cfg.get("target_overlap_percentage", 20))
                self.total_cols = int(stitch_cfg.get("x_steps", 5))
                self.total_rows = int(stitch_cfg.get("y_steps", 3))
                print(f"[Config] Grid from JSON: {self.total_cols}x{self.total_rows} "
                      f"overlap={self.target_overlap_percentage}%")
            except Exception as e:
                print(f"[Error] Failed to load grid params from JSON: {e}")
                raise

        # ── 3. Derived pixel geometry ─────────────────────────────────────────
        self.overlap_x_px = self.camera_full_width  * (self.target_overlap_percentage / 100.0)
        self.overlap_y_px = self.camera_full_height * (self.target_overlap_percentage / 100.0)
        self.step_x_px    = self.camera_full_width  - self.overlap_x_px
        self.step_y_px    = self.camera_full_height - self.overlap_y_px
        self.tile_size    = (self.camera_full_width, self.camera_full_height, 3)
        self.motion_increment_x_mm = (self.step_x_px * self.effective_pixel_size_um) / 1000.0
        self.motion_increment_y_mm = (self.step_y_px * self.effective_pixel_size_um) / 1000.0

        print(f"[Config] Step (pixels): {self.step_x_px:.1f} x {self.step_y_px:.1f}")
        print(f"[Config] Grid: cols={self.total_cols}, rows={self.total_rows}")
             
    def _contrast_stretching(self, img, low_percentile=2, high_percentile=98):
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        else:
            gray = img
        p_low = np.percentile(gray, low_percentile)
        p_high = np.percentile(gray, high_percentile)
        denom = (p_high - p_low) if (p_high - p_low) > 1e-6 else 1.0
        stretched = np.clip((gray - p_low) / denom * 255, 0, 255).astype(np.uint8)
        return stretched

    def _preprocess_image(self, img):
        stretched = self._contrast_stretching(img)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(stretched)

        denoised = cv2.medianBlur(enhanced, 3)
        gaussian = cv2.GaussianBlur(denoised, (5, 5), 1.0)
        sharpened = cv2.addWeighted(denoised, 1.5, gaussian, -0.5, 0)
        clipped = np.clip(sharpened, 0, 255).astype(np.uint8)

        upscaled = cv2.resize(
            clipped, None, fx=self.scale, fy=self.scale,
            interpolation=cv2.INTER_CUBIC
        )
        return upscaled

    def _get_good_matches(self, kp1, des1, kp2, des2, norm_type):
        if des1 is None or des2 is None:
            return []
        bf = cv2.BFMatcher(norm_type)
        raw = bf.knnMatch(des1, des2, k=2)
        good = []
        for m_n in raw:
            if len(m_n) == 2:
                m, n = m_n
                if m.distance < 0.75 * n.distance:
                    pt1 = kp1[m.queryIdx].pt
                    pt2 = kp2[m.trainIdx].pt
                    good.append((pt1, pt2))
        return good

    def _run_registration(self, r_gray, t_gray):
        # ORB
        kp_r_orb, des_r_orb = self.orb.detectAndCompute(r_gray, None)
        kp_t_orb, des_t_orb = self.orb.detectAndCompute(t_gray, None)
        # AKAZE
        kp_r_ak, des_r_ak = self.akaze.detectAndCompute(r_gray, None)
        kp_t_ak, des_t_ak = self.akaze.detectAndCompute(t_gray, None)

        good_orb = self._get_good_matches(kp_r_orb, des_r_orb, kp_t_orb, des_t_orb, cv2.NORM_HAMMING)
        good_ak  = self._get_good_matches(kp_r_ak,  des_r_ak,  kp_t_ak,  des_t_ak,  cv2.NORM_HAMMING)
        all_matches = good_orb + good_ak

        if len(all_matches) < self.MIN_MATCHES:
            return None, 0  # fail

        pts_r = np.float32([m[0] for m in all_matches])
        pts_t = np.float32([m[1] for m in all_matches])

        M, inliers = cv2.estimateAffinePartial2D(
            pts_t, pts_r,
            method=cv2.RANSAC,
            ransacReprojThreshold=3.0
        )
        if M is None:
            return None, 0

        # bring translation back to original scale (because we upscaled inputs)
        M[0, 2] /= self.scale
        M[1, 2] /= self.scale

        # score = inlier count (more stable than raw match count)
        score = int(np.sum(inliers)) if inliers is not None else len(all_matches)
        return M, score

    # _compute_edges_from_stage was removed and merged into _calculate_dynamic_crops to avoid clashing logic.

    def Find_Offsets(self, target_image, ref_image, vertical, reverse):
        """
        ORB+AKAZE affine registration (NO SIFT / homography / phase / sobel).
        Returns (dx, dy, score). On failure returns (0,0,0).

        Debug prints:
        - mode vertical/reverse
        - crop sizes and preprocessed sizes
        - dx/dy and score
        """

        if target_image is None or ref_image is None:
            self._dbg(f"[OFFFAIL] target/ref is None vertical={vertical} reverse={reverse}")
            return 0, 0, 0

        if target_image.dtype != np.uint8:
            target_image = target_image.astype(np.uint8)
        if ref_image.dtype != np.uint8:
            ref_image = ref_image.astype(np.uint8)

        # --- Crop geometry
        H, W = target_image.shape[:2]
        edge_x = min(self.edge_x, W - 1)
        edge_y = min(self.edge_y, H - 1)

        if not vertical:
            if not reverse:
                t_crop = target_image[:, :edge_x]
                r_crop = ref_image[:, W-edge_x:]
            else:
                t_crop = target_image[:, W-edge_x:]
                r_crop = ref_image[:, :edge_x]
        else:
            t_crop = target_image[:edge_y, :]
            r_crop = ref_image[H-edge_y:, :]

        if t_crop.size == 0 or r_crop.size == 0:
            self._dbg(f"[OFFFAIL] empty crop vertical={vertical} reverse={reverse} "
                    f"t_crop={getattr(t_crop,'shape',None)} r_crop={getattr(r_crop,'shape',None)}")
            return 0, 0, 0

        # preprocess (upscaled grayscale)
        t_gray = self._preprocess_image(t_crop)
        r_gray = self._preprocess_image(r_crop)

        M, score = self._run_registration(r_gray, t_gray)
        if M is None or score <= 0:
            self._dbg(f"[OFFFAIL] vertical={vertical} reverse={reverse} "
                    f"score={score} t_crop={t_crop.shape} r_crop={r_crop.shape} "
                    f"t_gray={t_gray.shape} r_gray={r_gray.shape}")
            return 0, 0, 0

        dx = int(round(M[0, 2]))
        dy = int(round(M[1, 2]))

        self._dbg(f"[OFFOK] vertical={vertical} reverse={reverse} dx={dx} dy={dy} score={score} "
                f"t_crop={t_crop.shape} r_crop={r_crop.shape} "
                f"t_gray={t_gray.shape} r_gray={r_gray.shape}")

        return dx, dy, score

    def set_secondary_image(self, image):
        """Set the secondary (label) image to be passed to OME-TIFF export."""
        self.secondary_image = image

    def remove_item(self):
        if self.queue:
            item = self.queue.popleft()
            self.removed_count += 1
            return item
        raise IndexError("Queue is empty.")

    def peek(self, index):
        if self.queue:
            # Map absolute index to relative queue position
            return self.queue.get_item(index - self.removed_count)
        raise IndexError("Queue is empty.")

    def return_xy(self, index):
        if self.queue:
            # Map absolute index to relative queue position
            return self.queue.get_xy(index - self.removed_count)
        raise IndexError("Queue is empty.")

    def run(self):
        self.process_frames_thread()
        print('entered run')
    
    def process_frames_thread(self):
        while int(self.wsi_dask.num_processed_frames) < self.total_frames:
            if self.queue.size() >= self.total_cols * 2:
                self.register_raster_group()
                for _ in range(self.total_cols):
                    self.remove_item()
            else:
                time.sleep(0.5)

        print('out of loop process frames thread')
        return True

    def _overlay_vertical_anchor_badge(self, tile, row, idx, ref_idx, dx, dy, score=None):
        """
        Draw a top banner on the tile marking it as the vertical anchor.
        Works on RGB uint8 tiles.
        """
        if tile is None:
            return tile

        # ensure uint8
        if tile.dtype != np.uint8:
            tile = tile.astype(np.uint8)

        h, w = tile.shape[:2]
        banner_h = min(140, h // 8)  # adaptive banner height

        # semi-opaque black banner
        overlay = tile.copy()
        cv2.rectangle(overlay, (0, 0), (w - 1, banner_h), (0, 0, 0), -1)

        alpha = 0.55
        tile[:banner_h, :, :] = cv2.addWeighted(overlay[:banner_h, :, :], alpha,
                                                tile[:banner_h, :, :], 1 - alpha, 0)

        # text (yellow)
        line1 = f"VERTICAL ANCHOR  row={row} idx={idx}  ref={ref_idx}"
        if score is None:
            line2 = f"dx={dx}  dy={dy}"
        else:
            line2 = f"dx={dx}  dy={dy}  score={score}"

        cv2.putText(tile, line1, (20, int(banner_h * 0.45)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3, cv2.LINE_AA)
        cv2.putText(tile, line2, (20, int(banner_h * 0.85)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3, cv2.LINE_AA)

        return tile

    def register_raster_group(self):
        """
        Stitch exactly ONE row.

        Correct logic:
        1) process_col(): compute vertical offsets per *spatial column* (pairs by X-sorted indices).
        This gives an absolute anchor for each tile from the tile above:
            center(curr) = center(prev_above) - (dx_v, dy_v)
        2) process_row(): compute horizontal offsets within this row (left->right spatial).
        3) Commit absolute centers:
        - First: apply vertical anchors for columns that succeeded (or single anchor applied to all).
        - Second: fill missing columns by horizontal propagation from nearest resolved tile.
        4) Trim using absolute centers only.
        5) Append row into wsi_dask.processed_da as a Dask array only.
        """

        row_start = int(self.wsi_dask.num_processed_frames)
        row = row_start // self.total_cols
        reverse_scan = (row % 2 == 1)

        self._dbg(f"\n[ROW] start row={row} row_start={row_start} reverse={reverse_scan}")

        # 1) Vertical offsets (populates self.row_vertical_offsets)
        self.process_col()

        # 2) Horizontal offsets within row (populates self._row_ref / self._row_off)
        self.process_row(row_start)

        # indices for this row
        row_indices = [row_start + i for i in range(self.total_cols)]

        def _xy(idx):
            return self.return_xy(idx)

        # spatial ordering (left->right)
        row_sorted = sorted(row_indices, key=lambda i: float(_xy(i)[0]))

        # Build col_of mapping to translate acquired index to spatial column
        col_of = {idx: spatial_col for spatial_col, idx in enumerate(row_sorted)}

        resolved = set()

        # Row 0 canonical anchor
        if row == 0:
            anchor_idx = row_sorted[0]
            col = col_of[anchor_idx]
            self.visit.set_visited(anchor_idx, col, row)
            self.visit.set_offsets(col, row, int(self.CANON_CENTER[0]), int(self.CANON_CENTER[1]))
            self._dbg(f"[ROW-ANCHOR] row=0 col={col} idx={anchor_idx} CANON center={self.CANON_CENTER}")

        # -------------------------------------------------------------------------
        # 3A) Apply vertical anchors for this row (row > 0)
        # -------------------------------------------------------------------------
        resolved = set()

        if row > 0:
            prev_row_start = row_start - self.total_cols
            prev_indices = [prev_row_start + i for i in range(self.total_cols)]
            prev_sorted = sorted(prev_indices, key=lambda i: float(self.return_xy(i)[0]))

            if getattr(self, "debug_vertical_all_cols", False):
                # Strategy B: compute/apply per-column vertical offsets
                for col in range(self.total_cols):
                    dx_v, dy_v, score, _pidx, _cidx = self.row_vertical_offsets.get(col, (0, 0, 0, None, None))
                    if score <= 0:
                        continue

                    prev_idx = prev_sorted[col]
                    curr_idx = row_sorted[col]

                    prev_center = self.visit.get_offsets(col, row - 1)
                    if prev_center == (0, 0) or prev_center is None:
                        prev_center = (int(self.prev_centerx), int(self.prev_centery))

                    cx = int(prev_center[0]) - int(dx_v)
                    cy = int(prev_center[1]) + self.nominal_step_y - int(dy_v)

                    self.visit.set_visited(curr_idx, col, row)
                    self.visit.set_offsets(col, row, cx, cy)
                    resolved.add(curr_idx)

                    self._dbg(
                        f"[V-ABS] row={row} col={col} curr_idx={curr_idx} above_idx={prev_idx} "
                        f"above_center={prev_center} dx_v={dx_v} dy_v={dy_v} -> center=({cx},{cy})"
                    )

            else:
                # If multiple columns computed (early rows), pick highest-score dy
                if len(self.row_vertical_offsets) > 1:
                    # Find best column
                    best_col = None
                    best_score = -1
                    best_dx, best_dy = 0, 0

                    for col, (dx_v, dy_v, score, _, _) in self.row_vertical_offsets.items():
                        if score > best_score:
                            best_score = score
                            best_col = col
                            best_dx = dx_v
                            best_dy = dy_v

                    self._dbg(f"[V-EARLY-BEST] row={row} picked col={best_col} dy={best_dy} dx={best_dx} score={best_score}")

                    anchor_col = best_col
                    dx_v = best_dx
                    dy_v = best_dy
                else:
                    # Original single-anchor fallback
                    anchor_col = next(iter(self.row_vertical_offsets.keys()))
                    dx_v, dy_v, score, _pidx, _cidx = self.row_vertical_offsets[anchor_col]

                if 'score' not in locals() or score <= 0:
                    self._dbg(f"[V-ABS-FAIL] row={row} anchor_col={anchor_col} score<=0; row will float")
                else:
                    prev_idx = prev_sorted[anchor_col]
                    curr_idx = row_sorted[anchor_col]

                    prev_center = self.visit.get_offsets(anchor_col, row - 1)
                    if prev_center == (0, 0) or prev_center is None:
                        prev_center = (int(self.prev_centerx), int(self.prev_centery))

                    cx = int(prev_center[0]) - int(dx_v)
                    cy = int(prev_center[1]) + self.nominal_step_y - int(dy_v)

                    self.visit.set_visited(curr_idx, anchor_col, row)
                    self.visit.set_offsets(anchor_col, row, cx, cy)
                    resolved.add(curr_idx)

                    print(f"[DY-APPLIED] row={row} anchor_col={anchor_col} dy_v={dy_v} prev_cy={int(prev_center[1])} new_cy={cy} advance={cy - int(prev_center[1])} px")

                    self._dbg(
                        f"[V-ROW-ABS] row={row} anchor_col={anchor_col} dx_v={dx_v} dy_v={dy_v} score={score} -> center=({cx},{cy})"
                    )

            # Rolling center update will be handled after propagation and trimming
            pass

        # -------------------------------------------------------------------------
        # 3B) Build horizontal bidirectional links and propagate absolute centers
        # -------------------------------------------------------------------------
        adj = {idx: [] for idx in row_indices}
        for idx, ref in getattr(self, "_row_ref", {}).items():
            if ref is not None:
                dx_h, dy_h = self._row_off.get(idx, (0, 0))
                # Forward link: idx = ref + (width-dx, -dy)
                adj[ref].append((idx, int(dx_h), int(dy_h), True))
                # Backward link: ref = idx - (width-dx, -dy)
                adj[idx].append((ref, int(dx_h), int(dy_h), False))

        # Collect all tiles that serve as potential bridges/anchors in this row
        row_anchors = [idx for idx, ref in getattr(self, "_row_ref", {}).items() if ref is None]

        if row == 0:
            # Row 0 fallback: any horizontal anchor is anchored to canon + nominal step
            for idx in row_anchors:
                if idx in resolved: continue
                col = col_of[idx]
                cx = int(self.CANON_CENTER[0]) + col * self.nominal_step_x
                cy = int(self.CANON_CENTER[1])
                self.visit.set_visited(idx, col, row)
                self.visit.set_offsets(col, row, cx, cy)
                resolved.add(idx)
                self._dbg(f"[ROW-0-ANCHOR] idx={idx} col={col} -> center=({cx},{cy})")
            starts = list(resolved)
        else:
            # Row > 0 fallback: use vertical anchors first
            starts = list(resolved)
            if not starts:
                # --- SNAKE-AWARE FALLBACK ---
                # Use ALL row anchors (tiles that didn't match a left neighbor)
                # and anchor them to the prev_center (the previous row's end)
                for idx in row_anchors:
                    col = col_of[idx]
                    # Since propagation failed vertically, we anchor to the previous row's end position
                    # but MUST account for the column difference to avoid drift.
                    # previous row ended at rolling center (prev_centerx).
                    # If prev row was FWD, prev_centerx is at col=Max.
                    # If prev row was REV, prev_centerx is at col=0.
                    
                    # More robust: just anchor based on the rolling center and the relative column shift.
                    prev_anchor_col = 0 if (row % 2 == 0) else (self.total_cols - 1)
                    col_shift = col - prev_anchor_col
                    
                    cx = int(self.prev_centerx) + col_shift * self.nominal_step_x
                    cy = int(self.prev_centery) + self.nominal_step_y
                    
                    self.visit.set_visited(idx, col, row)
                    self.visit.set_offsets(col, row, cx, cy)
                    resolved.add(idx)
                    self._dbg(f"[FALLBACK-ANCHOR] row={row} idx={idx} col={col} -> center=({cx},{cy})")
                starts = list(resolved)

        stack = starts[:]
        seen = set(starts)

        while stack:
            ref = stack.pop()
            rx, ry = _xy(ref)
            for idx, dx_h, dy_h, is_forward in adj.get(ref, []):
                if idx in seen:
                    continue

                col_ref = col_of[ref]
                ref_center = self.visit.get_offsets(col_ref, row)

                if is_forward:
                    cx = int(ref_center[0]) + self.nominal_step_x - dx_h
                    cy = int(ref_center[1]) - dy_h
                else:
                    cx = int(ref_center[0]) - (self.nominal_step_x - dx_h)
                    cy = int(ref_center[1]) + dy_h
                
                col_curr = col_of[idx]
                self.visit.set_visited(idx, col_curr, row)
                self.visit.set_offsets(col_curr, row, cx, cy)

                self._dbg(
                    f"[H-ABS-BIDI] row={row} idx={idx} col={col_curr} ref={ref} dir={'FWD' if is_forward else 'REV'} "
                    f"dx_h={dx_h} dy_h={dy_h} -> center=({cx},{cy})"
                )

                seen.add(idx)
                stack.append(idx)

        # -------------------------------------------------------------------------
        # 4) Trim row using absolute centers only + build a Dask row
        # -------------------------------------------------------------------------
        current_row_da = None
        row_tiles_fallback = []

        for spatial_col, idx in enumerate(row_sorted):
            img = self.peek(idx)

            if img is None:
                tile_np = np.full((self.height, self.width, 3), 255, dtype=np.uint8)
                self._dbg(f"[BLANK] row={row} idx={idx}")
            else:
                cx, cy = self.visit.get_offsets(spatial_col, row)

                if cx is None or cy is None:
                    # Final fallback: should be reached only if everything failed
                    prev_anchor_col = 0 if (row % 2 == 0) else (self.total_cols - 1)
                    col_shift = spatial_col - prev_anchor_col
                    cx = int(self.prev_centerx) + (col_shift * self.nominal_step_x)
                    cy = int(self.prev_centery) + self.nominal_step_y
                    self._dbg(f"[ABS-CENTER-MISS] row={row} idx={idx} col={spatial_col} using deep fallback center=({cx},{cy})")

                # is_anchor = only for the very last tile of the scan order in this row
                # (to set up the rolling center for the next row)
                last_acquired_in_row = (idx == row_start + self.total_cols - 1)

                tile_np = self.new_trim(
                    dx=0, dy=0,
                    img=img,
                    prev_centerx=cx,
                    prev_centery=cy,
                    is_anchor=last_acquired_in_row,
                    abs_center=(cx, cy),
                    row=row,
                    col=spatial_col
                )
            
            tile_da = self._as_dask_tile(tile_np)

            if getattr(self, "enable_blending", True):
                if current_row_da is None:
                    current_row_da = tile_da
                else:
                    # Use horizontal blending for tiles in a row
                    current_row_da = self.blend_hstack_dask(current_row_da, tile_da, self.blend_overlap_x)
            else:
                row_tiles_fallback.append(tile_da)
        
        if not getattr(self, "enable_blending", True):
            current_row_da = da.concatenate(row_tiles_fallback, axis=1)

        # -------------------------------------------------------------------------
        # 5) Append to mosaic (Dask-only)
        # -------------------------------------------------------------------------
        if getattr(self.wsi_dask, "processed_da", None) is None:
            self.wsi_dask.processed_da = current_row_da
        else:
            if getattr(self, "enable_blending", True):
                # Use vertical blending to join the new row to the slide
                self.wsi_dask.processed_da = self.blend_vstack_dask(
                    self.wsi_dask.processed_da, 
                    current_row_da, 
                    self.blend_overlap_y
                )
            else:
                self.wsi_dask.processed_da = da.concatenate([self.wsi_dask.processed_da, current_row_da], axis=0)

        self.wsi_dask.num_processed_frames += self.total_cols
        self._dbg(f"[ROW-OK] row={row} stitched total={self.wsi_dask.num_processed_frames}\n")
        return True

    def new_trim(
        self,
        dx, dy,
        img,
        prev_centerx,
        prev_centery,
        is_anchor=False,
        abs_center=None,   # <-- NEW: (center_x, center_y) if already solved
        row=0,             # <-- NEW: Current row index
        col=0,             # <-- NEW: Current spatial column index
    ):
        """
        If abs_center is provided, crop around that absolute center directly.
        Otherwise, use the old behavior: center = prev_center - (dx,dy).
        """

        if img is None:
            return np.full((self.height, self.width, 3), 255, dtype=np.uint8)

        # 1) choose global center
        if abs_center is not None and abs_center[0] is not None and abs_center[1] is not None:
            g_center_x, g_center_y = int(abs_center[0]), int(abs_center[1])
            dx_used, dy_used = 0, 0
        else:
            dx_used, dy_used = int(dx), int(dy)
            g_center_x = int(prev_centerx) - dx_used
            g_center_y = int(prev_centery) - dy_used

        # 2) MAP GLOBAL TO LOCAL
        # Propagation (Bugs A/B) uses global coords (+width/+height).
        # We must subtract the nominal advancement to keep the crop logical within the frame.
        center_x = g_center_x - (int(col) * self.nominal_step_x)
        center_y = g_center_y - (int(row) * self.nominal_step_y)

        # 3) compute crop rect around center
        half_w = self.width // 2
        half_h = self.height // 2

        left = int(center_x - half_w)
        top  = int(center_y - half_h)

        # clamp to image bounds
        if left < 0:
            left = 0
            self._dbg(f"[CLAMP] col={col} center_x={center_x} left out of bounds (0)")
        if top < 0:
            top = 0
            self._dbg(f"[CLAMP] row={row} center_y={center_y} top out of bounds (0)")

        if left + self.width > img.shape[1]:
            left = max(0, img.shape[1] - self.width)
            self._dbg(f"[CLAMP] col={col} center_x={center_x} right out of bounds ({img.shape[1]})")
        if top + self.height > img.shape[0]:
            top = max(0, img.shape[0] - self.height)
            self._dbg(f"[CLAMP] row={row} center_y={center_y} bottom out of bounds ({img.shape[0]})")

        tile = img[top:top + self.height, left:left + self.width].copy()

        # 4) persist global center (so others can reference it)
        # If you're using Visited offsets, store absolute center here.
        try:
            self.visit.set_offsets(col, row, g_center_x, g_center_y)
        except Exception:
            pass

        # 5) update rolling center only when requested
        if is_anchor:
            self.prev_centerx = g_center_x
            self.prev_centery = g_center_y

        # 6) debug
        self._dbg(
            f"[TRIM] col={col} row={row} "
            f"dx={dx_used} dy={dy_used} prev_center=({int(prev_centerx)},{int(prev_centery)}) "
            f"g_center=({g_center_x},{g_center_y}) l_center=({center_x},{center_y}) "
            f"crop(left={left},top={top},w={self.width},h={self.height}) "
            f"img_wh=({img.shape[1]},{img.shape[0]}) anchor={is_anchor} abs_center={abs_center is not None}"
        )

        print(f"[TRIM-OVERLAP] row={row} col={col} g_center_y={g_center_y} crop_top={top} crop_bottom={top + self.height} img_h={img.shape[0]}")

        return tile

    def process_col(self):
            """
            Vertical (top/bottom) offsets between row-1 and row for placement.

            Strategy B (debug, heavy): self.debug_vertical_all_cols=True
            - compute dx/dy for EVERY spatial column (left->right), store per-col.

            Strategy A (fast): self.debug_vertical_all_cols=False and self.vertical_single_anchor=True
            - compute dx/dy ONCE using a snake-aware anchor column:
                row odd  (REV): anchor = rightmost col (cols-1) of previous row
                row even (FWD): anchor = leftmost col (0) of previous row
            - store only that anchor col in row_vertical_offsets.

            Output:
            self.row_vertical_offsets[col] = (dx_v, dy_v, score, prev_idx, curr_idx)
            where col is spatial column index in left->right order.
            """

            row_start = int(self.wsi_dask.num_processed_frames)
            row = row_start // self.total_cols

            # init container every row
            self.row_vertical_offsets = {}

            if row == 0:
                self._dbg("[V-COL] row=0 no vertical offsets")
                return

            if row <= 2:  # Force all-column for first 3 rows to reduce early drift
                cols_to_do = list(range(self.total_cols))
                self._dbg(f"[V-COL] row={row} mode=ALL_EARLY (forced for drift control) n={len(cols_to_do)}")
            elif getattr(self, "debug_vertical_all_cols", False):
                cols_to_do = list(range(self.total_cols))
                self._dbg(f"[V-COL] row={row} mode=ALL_COLS n={len(cols_to_do)}")
            else:
                if getattr(self, "vertical_single_anchor", True):
                    anchor_col = (self.total_cols - 1) if (row % 2 == 1) else 0
                    cols_to_do = [anchor_col]
                    self._dbg(f"[V-COL] row={row} mode=SINGLE_ANCHOR anchor_col={anchor_col}")
                else:
                    cols_to_do = list(range(self.total_cols))
                    self._dbg(f"[V-COL] row={row} mode=DEFAULT_ALL_COLS n={len(cols_to_do)}")

            prev_row_start = row_start - self.total_cols

            prev_indices = [prev_row_start + i for i in range(self.total_cols)]
            curr_indices = [row_start + i for i in range(self.total_cols)]

            def _x(idx):
                x, _y = self.return_xy(idx)
                return float(x)

            # Spatial order left->right for BOTH rows (snake-safe)
            prev_sorted = sorted(prev_indices, key=_x)
            curr_sorted = sorted(curr_indices, key=_x)

            MIN_V_SCORE = 20  # keep your gating

            # --- choose which columns to compute ---
            if getattr(self, "debug_vertical_all_cols", False):
                cols_to_do = list(range(self.total_cols))
                self._dbg(f"[V-COL] row={row} mode=ALL_COLS n={len(cols_to_do)}")
            else:
                if getattr(self, "vertical_single_anchor", True):
                    # Snake-aware: row1 anchored using rightmost of row0, row2 using leftmost of row1, etc.
                    anchor_col = (self.total_cols - 1) if (row % 2 == 1) else 0
                    cols_to_do = [anchor_col]
                    self._dbg(f"[V-COL] row={row} mode=SINGLE_ANCHOR anchor_col={anchor_col}")
                else:
                    # if single_anchor disabled and debug disabled, default to all cols (safe)
                    cols_to_do = list(range(self.total_cols))
                    self._dbg(f"[V-COL] row={row} mode=DEFAULT_ALL_COLS n={len(cols_to_do)}")

            # --- compute requested vertical matches ---
            for col in cols_to_do:
                prev_idx = prev_sorted[col]
                curr_idx = curr_sorted[col]

                prev_img = self.peek(prev_idx)
                curr_img = self.peek(curr_idx)

                if prev_img is None or curr_img is None:
                    self.row_vertical_offsets[col] = (0, 0, 0, prev_idx, curr_idx)
                    self._dbg(f"[V-COL-FAIL] row={row} col={col} prev_idx={prev_idx} curr_idx={curr_idx} reason=missing")
                    
                    # ─────────────────────────────────────────────────────────────
                    # NEW: Print dy for EVERY column in this row (for tuning later)
                    # ─────────────────────────────────────────────────────────────
                    dx_v, dy_v, score, prev_idx, curr_idx = self.row_vertical_offsets[col]
                    if score > 0:
                        print(f"[DY-PER-COL] row={row} col={col} | dy={dy_v:+5d} px | score={score:3d} | prev_idx={prev_idx} curr_idx={curr_idx}")
                    else:
                        print(f"[DY-PER-COL] row={row} col={col} | dy= N/A (failed) | score={score:3d} | prev_idx={prev_idx} curr_idx={curr_idx}")
                    # ─────────────────────────────────────────────────────────────
                    
                    continue

                dx_v, dy_v, score = self.Find_Offsets(curr_img, prev_img, vertical=True, reverse=False)

                if score < MIN_V_SCORE:
                    self.row_vertical_offsets[col] = (0, 0, 0, prev_idx, curr_idx)
                    self._dbg(
                        f"[V-COL-FAIL] row={row} col={col} prev_idx={prev_idx} curr_idx={curr_idx} "
                        f"dx={int(dx_v)} dy={int(dy_v)} score={score} (below {MIN_V_SCORE})"
                    )
                    
                    # ─────────────────────────────────────────────────────────────
                    # NEW: Print dy for EVERY column in this row (for tuning later)
                    # ─────────────────────────────────────────────────────────────
                    dx_v, dy_v, score, prev_idx, curr_idx = self.row_vertical_offsets[col]
                    if score > 0:
                        print(f"[DY-PER-COL] row={row} col={col} | dy={dy_v:+5d} px | score={score:3d} | prev_idx={prev_idx} curr_idx={curr_idx}")
                    else:
                        print(f"[DY-PER-COL] row={row} col={col} | dy= N/A (failed) | score={score:3d} | prev_idx={prev_idx} curr_idx={curr_idx}")
                    # ─────────────────────────────────────────────────────────────
                    
                    continue

                self.row_vertical_offsets[col] = (int(dx_v), int(dy_v), int(score), prev_idx, curr_idx)
                self._dbg(
                    f"[V-COL-OK] row={row} col={col} prev_idx={prev_idx} curr_idx={curr_idx} "
                    f"dx={int(dx_v)} dy={int(dy_v)} score={score}"
                )

                # ─────────────────────────────────────────────────────────────
                # NEW: Print dy for EVERY column in this row (for tuning later)
                # ─────────────────────────────────────────────────────────────
                dx_v, dy_v, score, prev_idx, curr_idx = self.row_vertical_offsets[col]
                if score > 0:
                    print(f"[DY-PER-COL] row={row} col={col} | dy={dy_v:+5d} px | score={score:3d} | prev_idx={prev_idx} curr_idx={curr_idx}")
                else:
                    print(f"[DY-PER-COL] row={row} col={col} | dy= N/A (failed) | score={score:3d} | prev_idx={prev_idx} curr_idx={curr_idx}")
                # ─────────────────────────────────────────────────────────────

    def process_row(self, row_start: int):
        """
        Build horizontal links for ONE row.
        Stores:
            self._row_ref[idx] = reference idx
            self._row_off[idx] = (dx_h, dy_h)
        """

        row = row_start // self.total_cols

        self._row_ref = {}
        self._row_off = {}

        # indices belonging to this row
        row_indices = [row_start + i for i in range(self.total_cols)]

        # physical left → right ordering
        row_sorted = sorted(row_indices, key=lambda i: float(self.return_xy(i)[0]))

        last_good_idx = None
        last_good_img = None
        last_dx, last_dy = 0, 0

        for spatial_col, idx in enumerate(row_sorted):
            img = self.peek(idx)

            if img is None:
                self._row_ref[idx] = last_good_idx
                self._row_off[idx] = (last_dx, last_dy)
                self._dbg(
                    f"[ROW-BLANK] row={row} idx={idx} "
                    f"propagate=({last_dx},{last_dy}) ref={last_good_idx}"
                )
                continue

            if last_good_img is None:
                # first valid tile → row anchor
                self._row_ref[idx] = None
                self._row_off[idx] = (0, 0)
                last_good_idx = idx
                last_good_img = img
                last_dx, last_dy = 0, 0
                self._dbg(f"[ROW-ANCHOR] row={row} idx={idx}")
                continue

            dx, dy, score = self.Find_Offsets(
                img, last_good_img,
                vertical=False,
                reverse=False
            )

            if score <= 0:
                self._row_ref[idx] = last_good_idx
                self._row_off[idx] = (last_dx, last_dy)
                self._dbg(
                    f"[ROW-BLANK] row={row} idx={idx} "
                    f"offset_fail propagate=({last_dx},{last_dy}) ref={last_good_idx}"
                )
                continue

            self._row_ref[idx] = last_good_idx
            self._row_off[idx] = (int(dx), int(dy))
            self._dbg(
                f"[ROW-LINK] row={row} idx={idx} "
                f"ref={last_good_idx} dx={dx} dy={dy} score={score}"
            )

            last_good_idx = idx
            last_good_img = img
            last_dx, last_dy = int(dx), int(dy)

        # return physical anchor index (left-most tile)
        return row_sorted[0]

    def save_processed_images(self, ome_tiff_path, metadata_file_path=None):
        """
        Thin wrapper.
        All export logic lives in wsi_dask.wsi_da.save_processed_images().
        No behavior change from the original implementation.
        """

        # -----------------------------
        # Validate stitched image exists
        # -----------------------------
        if not hasattr(self.wsi_dask, "processed_da") or self.wsi_dask.processed_da is None:
            raise RuntimeError(
                "No stitched image available (wsi_dask.processed_da is None)"
            )


        pixel_size_um = self.effective_pixel_size_um

        # -----------------------------
        # Delegate export (unchanged behavior)
        # -----------------------------
        self.wsi_dask.save_processed_images(
            ome_tiff_path=ome_tiff_path,
            pixel_size_um=pixel_size_um,
            step_y=self.nominal_step_y,
        )


# Static method for multiprocessing
    @staticmethod
    def zarr_process(motion_current, queue,ome_tiff_path, metadata_file_path):
        print("zarr_process started.")
        process_collector = ProcessCollection(queue)
        print("ProcessCollection created.")
        process_collector.run()
        print("process_collector.run() completed.")

        label_image = motion_current.get_transformed_image()
        if label_image is not None:
            process_collector.set_secondary_image(label_image)
            print("Secondary camera image set.")
        else:
            print("No secondary camera image.")

        process_collector.save_processed_images(ome_tiff_path, metadata_file_path)
        print("Finished saving processed images.")
