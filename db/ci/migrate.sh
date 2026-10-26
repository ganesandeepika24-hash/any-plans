#!/usr/bin/env bash
# Applies db/migrations/*.sql in name order. Each file runs in one transaction
# together with recording its sha256 in schema_migrations.
#
# Already-applied files are skipped if unchanged; an edited one fails the run.
#
# Usage: PSQL='psql postgres://...' db/ci/migrate.sh
#        PSQL defaults to plain `psql`, so the usual PG* environment variables work.
set -euo pipefail
export LC_ALL=C

read -ra psql <<< "${PSQL:-psql}"
dir="$(cd "$(dirname "$0")/../migrations" && pwd)"

q() { "${psql[@]}" -X -q -v ON_ERROR_STOP=1 -tA "$@"; }

tracked=$(q -c "select to_regclass('public.schema_migrations') is not null")

for file in "$dir"/*.sql; do
  name=$(basename "$file")
  version=${name%%_*}
  sum=$(sha256sum "$file" | cut -d' ' -f1)

  recorded=""
  if [ "$tracked" = t ]; then
    recorded=$(q -c "select coalesce(checksum, 'none') from public.schema_migrations where version = '$version'")
  fi
  if [ -n "$recorded" ]; then
    if [ "$recorded" != "$sum" ]; then
      echo "$name was applied with checksum $recorded but is now $sum. Never edit an applied migration." >&2
      exit 1
    fi
    echo "$name: already applied"
    continue
  fi

  echo "$name: applying"
  {
    cat "$file"
    cat <<SQL

update public.schema_migrations set checksum = '$sum' where version = '$version' and filename = '$name';
do \$\$
begin
  if not exists (select 1 from public.schema_migrations where version = '$version' and checksum = '$sum') then
    raise exception '$name did not record itself in schema_migrations';
  end if;
end \$\$;
SQL
  } | q -1 -f -
  tracked=t
done
