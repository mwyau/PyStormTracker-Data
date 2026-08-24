; Generate compact NCL/Spherepack spectral references for this repository.
;
; The default sources are the full DJF ERA5 Release assets staged in
; release-data/.  Callers may override src25, src025, release_dir, or outdir.
; Example (NCL 6.6.2, run from the Data repository root):
;
;   ncl scripts/ncl/generate_msl_spectral_references.ncl
;
; The first frame of each source is checked explicitly and must be
; 2025-12-01 00:00 before it is used for the compact comparison.

load "$NCARG_ROOT/lib/ncarg/nclscripts/csm/contributed.ncl"

begin
    if (.not. isvar("release_dir")) then
        release_dir = "release-data"
    end if
    if (.not. isvar("src25")) then
        src25 = release_dir + "/era5_msl_2025-2026_djf_2.5x2.5.nc"
    end if
    if (.not. isvar("src025")) then
        src025 = release_dir + "/era5_msl_2025-2026_djf_0.25x0.25.nc"
    end if
    if (.not. isvar("outdir")) then
        outdir = "parity/ncl/"
    end if

    source_files = (/src25, src025/)
    resolutions = (/"2.5x2.5", "0.25x0.25"/)
    setfileoption("nc", "Format", "NetCDF4")

    do index = 0, 1
        source = addfile(source_files(index), "r")
        if (isfilevar(source, "valid_time")) then
            source_times = source->valid_time
        else if (isfilevar(source, "time")) then
            source_times = source->time
        else
            print("source has no valid_time or time coordinate: " + source_files(index))
            exit
        end if
        end if

        calendar_values = cd_calendar(source_times(0), 0)
        if (calendar_values(0) .ne. 2025 .or. calendar_values(1) .ne. 12 .or. calendar_values(2) .ne. 1 .or. calendar_values(3) .ne. 0) then
            print("first source frame is not 2025-12-01 00:00: " + source_files(index))
            exit
        end if

        raw = source->msl(0,:,:)
        dimensions = dimsizes(raw)
        lat = todouble(source->latitude)
        lon = todouble(source->longitude)
        lat@units = "degrees_north"
        lat@long_name = "latitude"
        lat@standard_name = "latitude"
        lon@units = "degrees_east"
        lon@long_name = "longitude"
        lon@standard_name = "longitude"

        coefficients = shaeC(raw)

        t0_42_coefficients = coefficients
        t0_42_coefficients(:,43:,:) = 0.0
        t0_42 = shseC(t0_42_coefficients, dimensions(1))

        t5_42_coefficients = t0_42_coefficients
        t5_42_coefficients(:,0:4,:) = 0.0
        t5_42 = shseC(t5_42_coefficients, dimensions(1))

        t0_42@long_name = "Mean sea level pressure (T0-42)"
        t0_42@units = "Pa"
        t0_42@standard_name = "air_pressure_at_mean_sea_level"
        t0_42!0 = "latitude"
        t0_42!1 = "longitude"
        t0_42&latitude = lat
        t0_42&longitude = lon

        t5_42@long_name = "Mean sea level pressure (T5-42)"
        t5_42@units = "Pa"
        t5_42@standard_name = "air_pressure_at_mean_sea_level"
        t5_42!0 = "latitude"
        t5_42!1 = "longitude"
        t5_42&latitude = lat
        t5_42&longitude = lon

        base = "era5_msl_2025-12-01_0000_" + resolutions(index)
        output = addfile(outdir + base + "_t0-42.nc", "c")
        output->msl = t0_42
        delete(output)
        output = addfile(outdir + base + "_t5-42.nc", "c")
        output->msl = t5_42
        delete(output)

        delete([/source, source_times, calendar_values, raw, dimensions, lat, lon, coefficients, t0_42_coefficients, t0_42, t5_42_coefficients, t5_42/])
    end do
end
