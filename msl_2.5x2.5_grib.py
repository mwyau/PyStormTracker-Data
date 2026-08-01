import cdsapi

dataset = "reanalysis-era5-single-levels"
request = {
    "product_type": ["reanalysis"],
    "variable": ["mean_sea_level_pressure"],
    "date": "2025-12-01/2026-02-28",
    "time": [
        "00:00", "06:00", "12:00",
        "18:00"
    ],
    "grid": ["2.5", "2.5"],
    "data_format": "grib",
    "download_format": "unarchived"
}
filename = "era5_msl_2025-2026_djf_2.5x2.5.grib"

client = cdsapi.Client()
client.retrieve(dataset, request, filename)