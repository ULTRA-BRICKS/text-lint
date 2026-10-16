The importer reads one CSV file per run and writes rows to the `events` table. A row with
a missing timestamp is skipped and counted in the run log. On a 2 GB file the run takes
about four minutes on the build server.

Set `IMPORT_BATCH=500` to change the batch size; the default is 1000.
