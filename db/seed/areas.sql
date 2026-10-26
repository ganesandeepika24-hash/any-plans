-- London's 33 boroughs (including the City of London) and named neighbourhoods.
-- Safe to run repeatedly. Needs cities.sql first: city_id is the city's slug.
-- Centre points from OpenStreetMap via Nominatim, looked up 2026-10-26; the OSM
-- object is noted on each row (© OpenStreetMap contributors, ODbL). Borough
-- points are boundary centroids, not town centres.
insert into public.areas (id, name, kind, aliases, centre_geom, default_radius_km, city_id)
select v.id, v.name, v.kind, v.aliases, v.centre_geom::extensions.geography, v.default_radius_km, 'london'
from (values
  ('london-barking-and-dagenham', 'Barking and Dagenham', 'borough', array['Barking', 'Dagenham']::text[], 'SRID=4326;POINT(0.150505 51.554117)', 6),  -- relation/185483
  ('london-barnet', 'Barnet', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.211689 51.612745)', 6),  -- relation/51831
  ('london-bexley', 'Bexley', 'borough', '{}'::text[], 'SRID=4326;POINT(0.145742 51.461994)', 6),  -- relation/51903
  ('london-brent', 'Brent', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.275906 51.563996)', 6),  -- relation/75767
  ('london-bromley', 'Bromley', 'borough', '{}'::text[], 'SRID=4326;POINT(0.061709 51.366857)', 6),  -- relation/152126
  ('london-camden', 'Camden', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.162480 51.542797)', 6),  -- relation/51827
  ('london-city-of-london', 'City of London', 'borough', array['The City', 'Square Mile']::text[], 'SRID=4326;POINT(-0.091998 51.515618)', 6),  -- relation/51800
  ('london-croydon', 'Croydon', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.064310 51.355056)', 6),  -- relation/51907
  ('london-ealing', 'Ealing', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.314294 51.525085)', 6),  -- relation/181321
  ('london-enfield', 'Enfield', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.080941 51.648770)', 6),  -- relation/51841
  ('london-greenwich', 'Greenwich', 'borough', '{}'::text[], 'SRID=4326;POINT(0.048831 51.468626)', 6),  -- relation/51902
  ('london-hackney', 'Hackney', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.047669 51.548882)', 6),  -- relation/51806
  ('london-hammersmith-and-fulham', 'Hammersmith and Fulham', 'borough', array['Hammersmith', 'Fulham']::text[], 'SRID=4326;POINT(-0.227878 51.498314)', 6),  -- relation/184484
  ('london-haringey', 'Haringey', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.105438 51.587936)', 6),  -- relation/51814
  ('london-harrow', 'Harrow', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.337305 51.596827)', 6),  -- relation/181292
  ('london-havering', 'Havering', 'borough', '{}'::text[], 'SRID=4326;POINT(0.249813 51.557926)', 6),  -- relation/185478
  ('london-hillingdon', 'Hillingdon', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.448335 51.542519)', 6),  -- relation/183779
  ('london-hounslow', 'Hounslow', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.380131 51.461733)', 6),  -- relation/51848
  ('london-islington', 'Islington', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.101658 51.547035)', 6),  -- relation/51821
  ('london-kensington-and-chelsea', 'Kensington and Chelsea', 'borough', array['Kensington', 'Chelsea', 'RBKC']::text[], 'SRID=4326;POINT(-0.200789 51.503795)', 6),  -- relation/51793
  ('london-kingston-upon-thames', 'Kingston upon Thames', 'borough', array['Kingston']::text[], 'SRID=4326;POINT(-0.276981 51.381776)', 6),  -- relation/51909
  ('london-lambeth', 'Lambeth', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.121313 51.460466)', 6),  -- relation/184710
  ('london-lewisham', 'Lewisham', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.012475 51.453521)', 6),  -- relation/184724
  ('london-merton', 'Merton', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.188097 51.410870)', 6),  -- relation/51905
  ('london-newham', 'Newham', 'borough', '{}'::text[], 'SRID=4326;POINT(0.029309 51.530016)', 6),  -- relation/185505
  ('london-redbridge', 'Redbridge', 'borough', '{}'::text[], 'SRID=4326;POINT(0.069796 51.586287)', 6),  -- relation/65598
  ('london-richmond-upon-thames', 'Richmond upon Thames', 'borough', array['Richmond']::text[], 'SRID=4326;POINT(-0.307639 51.440553)', 6),  -- relation/151795
  ('london-southwark', 'Southwark', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.069019 51.465255)', 6),  -- relation/8450265
  ('london-sutton', 'Sutton', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.173605 51.357443)', 6),  -- relation/17529
  ('london-tower-hamlets', 'Tower Hamlets', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.035056 51.514615)', 6),  -- relation/51805
  ('london-waltham-forest', 'Waltham Forest', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.017739 51.598263)', 6),  -- relation/65595
  ('london-wandsworth', 'Wandsworth', 'borough', '{}'::text[], 'SRID=4326;POINT(-0.199508 51.451901)', 6),  -- relation/51906
  ('london-westminster', 'Westminster', 'borough', array['City of Westminster']::text[], 'SRID=4326;POINT(-0.137149 51.497321)', 6),  -- relation/51781
  ('london-soho', 'Soho', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.135049 51.514065)', 1),  -- relation/17710512
  ('london-shoreditch', 'Shoreditch', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.079893 51.526669)', 1),  -- node/27365029
  ('london-camden-town', 'Camden Town', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.142757 51.539253)', 1),  -- node/399607277
  ('london-south-bank', 'South Bank', 'neighbourhood', array['Southbank']::text[], 'SRID=4326;POINT(-0.113420 51.506532)', 1),  -- node/450524790
  ('london-covent-garden', 'Covent Garden', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.122677 51.511736)', 1),  -- node/2290086954
  ('london-kings-cross', 'King''s Cross', 'neighbourhood', array['Kings Cross']::text[], 'SRID=4326;POINT(-0.122413 51.529108)', 1),  -- node/3936965089
  ('london-brixton', 'Brixton', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.114802 51.463391)', 1),  -- node/21521237
  ('london-peckham', 'Peckham', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.069932 51.473412)', 1),  -- node/207141595
  ('london-dalston', 'Dalston', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.076013 51.543212)', 1),  -- node/27365025
  ('london-hackney-wick', 'Hackney Wick', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.025467 51.544354)', 1),  -- node/3939373094
  ('london-notting-hill', 'Notting Hill', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.205527 51.510999)', 1),  -- node/2268352172
  ('london-clapham', 'Clapham', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.138627 51.461447)', 1),  -- node/27554267
  ('london-mayfair', 'Mayfair', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.147058 51.511087)', 1),  -- node/26745366
  ('london-marylebone', 'Marylebone', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.151963 51.519637)', 1),  -- node/27467066
  ('london-fitzrovia', 'Fitzrovia', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.138355 51.522943)', 1),  -- node/27467065
  ('london-bermondsey', 'Bermondsey', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.063268 51.497012)', 1),  -- node/27365035
  ('london-borough', 'Borough', 'neighbourhood', array['The Borough', 'Borough Market']::text[], 'SRID=4326;POINT(-0.093216 51.501255)', 1),  -- node/27553712
  ('london-angel', 'Angel', 'neighbourhood', array['The Angel']::text[], 'SRID=4326;POINT(-0.106114 51.533195)', 1),  -- node/4001592822
  ('london-greenwich-town-centre', 'Greenwich town centre', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(-0.004542 51.482084)', 1),  -- node/27365302
  ('london-stratford', 'Stratford', 'neighbourhood', '{}'::text[], 'SRID=4326;POINT(0.001621 51.540688)', 1)   -- node/27365046
) as v (id, name, kind, aliases, centre_geom, default_radius_km)
on conflict (id) do update set
  city_id           = excluded.city_id,
  name              = excluded.name,
  kind              = excluded.kind,
  aliases           = excluded.aliases,
  centre_geom       = excluded.centre_geom,
  default_radius_km = excluded.default_radius_km;
