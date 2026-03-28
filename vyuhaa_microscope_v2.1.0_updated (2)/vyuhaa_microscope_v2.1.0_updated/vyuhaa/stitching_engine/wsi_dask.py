import os
import dask.array as da
import numpy as np
import tifffile
import pyvips
import time

class wsi_da:
    def __init__(self):
        self.processed_da = None
        self.num_processed_frames = 0
        self.secondary_image = None

    def set_secondary_image(self, image):
        self.secondary_image = image

    def save_processed_images(self, ome_tiff_path, pixel_size_um=1.0, step_y=None):
        """
        Save the stitched Dask array as an OME-TIFF with pyramids and metadata.
        Uses PyVips for efficient large-image handling.
        """
        if self.processed_da is None:
            raise RuntimeError("No processed Dask array to save.")

        start_time = time.time()
        print(f"Exporting OME-TIFF to: {ome_tiff_path}")

        # 1. Convert Dask to NumPy (This triggers the computation of the mosaic)
        # For very large slides, we use a memory-mapped file or stream to PyVips.
        # Here we compute it into a NumPy array first.
        full_image = self.processed_da.compute()
        h, w, c = full_image.shape
        print(f"Mosaic computed: {w}x{h} pixels. Time: {time.time() - start_time:.2f}s")

        # 2. Create PyVips image from NumPy
        # pyvips uses (width, height, bands) convention
        vips_img = pyvips.Image.new_from_array(full_image)
        
        # 3. Build OME-XML Metadata
        # We include pixel size for calibrated measurements in QuPath/ImageJ.
        ome_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:schemaLocation="http://www.openmicroscopy.org/Schemas/OME/2016-06 http://www.openmicroscopy.org/Schemas/OME/2016-06/ome.xsd">
    <Image ID="Image:0" Name="Stitched Mosaic">
        <Pixels BigEndian="false"
                DimensionOrder="XYCZT"
                ID="Pixels:0"
                Interleaved="true"
                SizeC="{c}"
                SizeT="1"
                SizeX="{w}"
                SizeY="{h}"
                SizeZ="1"
                Type="uint8"
                PhysicalSizeX="{pixel_size_um}"
                PhysicalSizeY="{pixel_size_um}"
                PhysicalSizeXUnit="µm"
                PhysicalSizeYUnit="µm">
            <Channel ID="Channel:0:0" SamplesPerPixel="3"><LightPath/></Channel>
        </Pixels>
    </Image>
</OME>"""

        # 4. Save as OME-TIFF with Pyramids and Compreesion
        # We use 'tile' and 'pyramid' for fast zoom in viewers.
        os.makedirs(os.path.dirname(ome_tiff_path), exist_ok=True)
        
        vips_img.tiffsave(
            ome_tiff_path,
            compression="jpeg",
            tile=True,
            tile_width=256,
            tile_height=256,
            pyramid=True,
            properties=True,
            description=ome_xml
        )
        
        print(f"OME-TIFF saved successfully. Total export time: {time.time() - start_time:.2f}s")
        
        # 5. Clean up memory
        del full_image
        import gc
        gc.collect()
