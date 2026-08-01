import cdsapi

dataset = "reanalysis-era5-pressure-levels"
request = {
    "product_type": ["reanalysis"],
    "variable": [
        "u_component_of_wind",
        "v_component_of_wind"
    ],
    "date": "2025-12-01/2026-02-28",
    "time": [
        "00:00", "06:00", "12:00",
        "18:00"
    ],
    "pressure_level": ["850"],
    "grid": ["2.5", "2.5"],
    "data_format": "netcdf",
    "download_format": "unarchived"
}
filename = "era5_uv850_2025-2026_djf_2.5x2.5.nc"

client = cdsapi.Client()
client.retrieve(dataset, request, filename)
