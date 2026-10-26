-- London only. Safe to run repeatedly.
-- Centre: the Charles I statue at Charing Cross, OSM node/12877095809 (© OpenStreetMap contributors, ODbL).
insert into public.cities
  (id, name, country, timezone, currency, centre_geom, default_radius_km, holiday_region, travel_time_provider, active)
values
  ('london', 'London', 'GB', 'Europe/London', 'GBP', 'SRID=4326;POINT(-0.127654 51.507333)', 10,
   'england-and-wales', 'tfl', true)
on conflict (id) do update set
  name                 = excluded.name,
  country              = excluded.country,
  timezone             = excluded.timezone,
  currency             = excluded.currency,
  centre_geom          = excluded.centre_geom,
  default_radius_km    = excluded.default_radius_km,
  holiday_region       = excluded.holiday_region,
  travel_time_provider = excluded.travel_time_provider,
  active               = excluded.active;
