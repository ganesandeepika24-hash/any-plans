-- London's calendar: festivals, observances, school half-terms and events whose dates
-- move from year to year (decision 0019). Safe to run repeatedly. Needs cities.sql first.
-- Fixed-date days (Christmas Day, Bonfire Night) and bank holidays aren't here: the model
-- gives those as dates, and holidays.py has gov.uk's bank holidays.
-- Festival dates from claude/festival-dates-2026-2027 (checked 27 Oct 2026). Half-terms
-- from Haringey Council's published 2026 to 2027 term dates; Southwark's page agrees.
-- Most London boroughs share these weeks, but academies can set their own.
-- Pride in London and Notting Hill Carnival 2027 are left out: no confirmed dates, so
-- the app asks for one. Refresh before 1 September 2027 (decision 0019).
insert into public.calendar_dates
  (id, name, aliases, kind, starts_on, ends_on, estimate, source, checked_on, city_id)
select v.id, v.name, v.aliases, v.kind, v.starts_on::date, v.ends_on::date, v.estimate, v.source, v.checked_on::date, 'london'
from (values
  ('london-2026-october-half-term', 'October half-term', array['half-term', 'autumn half-term']::text[], 'school_holiday', '2026-10-26', '2026-10-30', false, 'Haringey Council term dates 2026 to 2027 (haringey.gov.uk)', '2026-10-28'),
  ('london-2026-diwali', 'Diwali', array['Deepavali', 'Divali', 'Lakshmi Puja']::text[], 'festival', '2026-11-08', '2026-11-08', false, 'Vedantu, via festival-dates-2026-2027', '2026-10-27'),
  ('london-2026-remembrance-sunday', 'Remembrance Sunday', '{}'::text[], 'observance', '2026-11-08', '2026-11-08', false, 'Second Sunday of November; owner confirmed', '2026-10-28'),
  ('london-2026-hanukkah', 'Hanukkah', array['Chanukah', 'Hannukah', 'Chanukkah']::text[], 'festival', '2026-12-04', '2026-12-12', false, 'Farmer''s Almanac, via festival-dates-2026-2027', '2026-10-27'),
  ('london-2027-chinese-new-year', 'Chinese New Year', array['Lunar New Year', 'Spring Festival']::text[], 'festival', '2027-02-06', '2027-02-06', false, 'ChinaTravel, via festival-dates-2026-2027', '2026-10-27'),
  ('london-2027-pancake-day', 'Pancake Day', array['Shrove Tuesday']::text[], 'observance', '2027-02-09', '2027-02-09', false, '47 days before Easter Sunday (28 March 2027, from gov.uk''s Good Friday); owner confirmed', '2026-10-28'),
  ('london-2027-february-half-term', 'February half-term', array['half-term', 'spring half-term', 'Feb half-term']::text[], 'school_holiday', '2027-02-15', '2027-02-19', false, 'Haringey Council term dates 2026 to 2027 (haringey.gov.uk)', '2026-10-28'),
  ('london-2027-mothers-day', 'Mother''s Day', array['Mothering Sunday']::text[], 'observance', '2027-03-07', '2027-03-07', false, 'Three weeks before Easter Sunday (28 March 2027); owner confirmed', '2026-10-28'),
  ('london-2027-eid-al-fitr', 'Eid al-Fitr', array['Eid', 'Eid ul-Fitr']::text[], 'festival', '2027-03-09', '2027-03-10', true, 'Khaleej Times (UAE Astronomy Centre), via festival-dates-2026-2027; 10 March if the moon isn''t sighted', '2026-10-27'),
  ('london-2027-vaisakhi', 'Vaisakhi', array['Baisakhi']::text[], 'festival', '2027-04-14', '2027-04-14', false, 'Drik Panchang (New Delhi date), via festival-dates-2026-2027', '2026-10-27'),
  ('london-2027-chelsea-flower-show', 'Chelsea Flower Show', array['RHS Chelsea Flower Show', 'RHS Chelsea']::text[], 'event', '2027-05-18', '2027-05-22', false, 'RHS, via festival-dates-2026-2027', '2026-10-27'),
  ('london-2027-may-half-term', 'May half-term', array['half-term', 'summer half-term', 'Whitsun half-term']::text[], 'school_holiday', '2027-05-31', '2027-06-04', false, 'Haringey Council term dates 2026 to 2027 (haringey.gov.uk)', '2026-10-28')
) as v (id, name, aliases, kind, starts_on, ends_on, estimate, source, checked_on)
on conflict (id) do update set
  name       = excluded.name,
  aliases    = excluded.aliases,
  kind       = excluded.kind,
  starts_on  = excluded.starts_on,
  ends_on    = excluded.ends_on,
  estimate   = excluded.estimate,
  source     = excluded.source,
  checked_on = excluded.checked_on,
  city_id    = excluded.city_id;
