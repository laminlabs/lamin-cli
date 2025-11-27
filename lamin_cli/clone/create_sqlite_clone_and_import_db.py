import lamindb_setup as ln_setup

modules_without_lamindb = ln_setup.settings.instance.modules
modules_complete = modules_without_lamindb.copy()
modules_complete.add("lamindb")

ln_setup.init(
    storage="{instance_name}-clone", modules=f"{','.join(modules_without_lamindb)}"
)

import lamindb

ln_setup.io.import_db(
    module_names={list(modules_complete)}, input_dir="{export_dir}", if_exists="replace"
)

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("PRAGMA wal_checkpoint(FULL)")

from django.db import connections

connections.close_all()
