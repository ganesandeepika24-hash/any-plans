-- London's calendar: festivals, observances, school half-terms and events whose dates
-- move from year to year (decision 0019). Safe to run repeatedly. Needs cities.sql first.
-- Bank holidays aren't here: holidays.py has gov.uk's. Fixed-date days that people name
-- as events (Halloween, Bonfire Night, Christmas Day, Boxing Day, New Year's Eve, Burns
-- Night, St George's Day) are, since 29 Oct 2026, so a reply that names one as an event
-- still finds its date (decision 0019 update; the second baseline's date questions).
-- Festival dates from claude/festival-dates-2026-2027 (checked 27 Oct 2026). Half-terms
-- from Haringey Council's published 2026 to 2027 term dates; Southwark's page agrees.
-- Most London boroughs share these weeks, but academies can set their own.
-- area_id is an entry's venue area, used when the person names no place (decision 0021).
-- Needs areas.sql first.
-- Pride in London and Notting Hill Carnival 2027 are left out: no confirmed dates, so
-- the app asks for one. Refresh before 1 September 2027 (decision 0019).
insert into public.calendar_dates
  (id, name, aliases, kind, starts_on, ends_on, estimate, source, checked_on, area_id, city_id)
select v.id, v.name, v.aliases, v.kind, v.starts_on::date, v.ends_on::date, v.estimate, v.source, v.checked_on::date, v.area_id, 'london'
from (values
  ('london-2026-october-half-term', 'October half-term', array['half-term', 'autumn half-term']::text[], 'school_holiday', '2026-10-26', '2026-10-30', false, 'Haringey Council term dates 2026 to 2027 (haringey.gov.uk)', '2026-10-28', null),
  ('london-2026-halloween', 'Halloween', array['Hallowe''en', 'All Hallows'' Eve']::text[], 'observance', '2026-10-31', '2026-10-31', false, 'Eve of All Saints'' Day, 1 November (Church of England, Common Worship calendar, churchofengland.org)', '2026-10-29', null),
  ('london-2026-bonfire-night', 'Bonfire Night', array['Guy Fawkes Night', 'Fireworks Night']::text[], 'observance', '2026-11-05', '2026-11-05', false, 'Gunpowder Plot, 5 November 1605 (UK Parliament, tickets.parliament.uk)', '2026-10-29', null),
  ('london-2026-diwali', 'Diwali', array['Deepavali', 'Divali', 'Lakshmi Puja']::text[], 'festival', '2026-11-08', '2026-11-08', false, 'Vedantu, via festival-dates-2026-2027', '2026-10-27', null),
  ('london-2026-remembrance-sunday', 'Remembrance Sunday', '{}'::text[], 'observance', '2026-11-08', '2026-11-08', false, 'Second Sunday of November; owner confirmed', '2026-10-28', null),
  ('london-2026-hanukkah', 'Hanukkah', array['Chanukah', 'Hannukah', 'Chanukkah']::text[], 'festival', '2026-12-04', '2026-12-12', false, 'Farmer''s Almanac, via festival-dates-2026-2027', '2026-10-27', null),
  ('london-2026-christmas-day', 'Christmas Day', array['Xmas Day']::text[], 'festival', '2026-12-25', '2026-12-25', false, 'gov.uk bank holidays, England and Wales (gov.uk/bank-holidays.json)', '2026-10-29', null),
  ('london-2026-boxing-day', 'Boxing Day', '{}'::text[], 'observance', '2026-12-26', '2026-12-26', false, '26 December; the bank holiday is Monday 28 December, the substitute day (gov.uk/bank-holidays.json)', '2026-10-29', null),
  ('london-2026-new-years-eve', 'New Year''s Eve', array['NYE']::text[], 'observance', '2026-12-31', '2026-12-31', false, 'The day before New Year''s Day, 1 January 2027 (gov.uk/bank-holidays.json)', '2026-10-29', null),
  ('london-2027-burns-night', 'Burns Night', array['Burns Supper']::text[], 'observance', '2027-01-25', '2027-01-25', false, 'Each year on 25 January (Scotland.org, the Scottish Government''s official site)', '2026-10-29', null),
  ('london-2027-chinese-new-year', 'Chinese New Year', array['Lunar New Year', 'Spring Festival']::text[], 'festival', '2027-02-06', '2027-02-06', false, 'ChinaTravel, via festival-dates-2026-2027', '2026-10-27', null),
  ('london-2027-pancake-day', 'Pancake Day', array['Shrove Tuesday']::text[], 'observance', '2027-02-09', '2027-02-09', false, '47 days before Easter Sunday (28 March 2027, from gov.uk''s Good Friday); owner confirmed', '2026-10-28', null),
  ('london-2027-february-half-term', 'February half-term', array['half-term', 'spring half-term', 'Feb half-term']::text[], 'school_holiday', '2027-02-15', '2027-02-19', false, 'Haringey Council term dates 2026 to 2027 (haringey.gov.uk)', '2026-10-28', null),
  ('london-2027-mothers-day', 'Mother''s Day', array['Mothering Sunday']::text[], 'observance', '2027-03-07', '2027-03-07', false, 'Three weeks before Easter Sunday (28 March 2027); owner confirmed', '2026-10-28', null),
  ('london-2027-eid-al-fitr', 'Eid al-Fitr', array['Eid', 'Eid ul-Fitr']::text[], 'festival', '2027-03-09', '2027-03-10', true, 'Khaleej Times (UAE Astronomy Centre), via festival-dates-2026-2027; 10 March if the moon isn''t sighted', '2026-10-27', null),
  ('london-2027-vaisakhi', 'Vaisakhi', array['Baisakhi']::text[], 'festival', '2027-04-14', '2027-04-14', false, 'Drik Panchang (New Delhi date), via festival-dates-2026-2027', '2026-10-27', null),
  ('london-2027-st-georges-day', 'St George''s Day', array['Saint George''s Day']::text[], 'observance', '2027-04-23', '2027-04-23', false, 'George, Martyr, Patron of England, 23 April (Church of England, Common Worship calendar); English Heritage', '2026-10-29', null),
  ('london-2027-chelsea-flower-show', 'Chelsea Flower Show', array['RHS Chelsea Flower Show', 'RHS Chelsea']::text[], 'event', '2027-05-18', '2027-05-22', false, 'RHS, via festival-dates-2026-2027', '2026-10-27', 'london-kensington-and-chelsea'),
  ('london-2027-may-half-term', 'May half-term', array['half-term', 'summer half-term', 'Whitsun half-term']::text[], 'school_holiday', '2027-05-31', '2027-06-04', false, 'Haringey Council term dates 2026 to 2027 (haringey.gov.uk)', '2026-10-28', null)
) as v (id, name, aliases, kind, starts_on, ends_on, estimate, source, checked_on, area_id)
on conflict (id) do update set
  name       = excluded.name,
  aliases    = excluded.aliases,
  kind       = excluded.kind,
  starts_on  = excluded.starts_on,
  ends_on    = excluded.ends_on,
  estimate   = excluded.estimate,
  source     = excluded.source,
  checked_on = excluded.checked_on,
  area_id    = excluded.area_id,
  city_id    = excluded.city_id;
