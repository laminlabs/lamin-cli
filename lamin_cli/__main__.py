import argparse
from importlib.metadata import PackageNotFoundError, version


class instance:
    storage_root = """local dir, s3://bucket_name, gs://bucket_name"""
    db = """postgres database connection URL, do not pass for SQLite"""
    name = """instance name"""
    schema = """comma-separated string of schema modules"""


class user:
    email = """user email"""
    password = """API key or legacy password"""
    uid = """universal user ID"""
    handle = "unique handle"
    name = "full name"


description_cli = "Configure LaminDB and perform simple actions."
parser = argparse.ArgumentParser(
    description=description_cli, formatter_class=argparse.RawTextHelpFormatter
)
subparsers = parser.add_subparsers(dest="command")

# init instance
init = subparsers.add_parser("init", help="init a lamindb instance")
aa = init.add_argument
aa("--storage", type=str, metavar="storage", help=instance.storage_root)
aa("--db", type=str, metavar="db", default=None, help=instance.db)
aa("--schema", type=str, metavar="schema", default=None, help=instance.schema)
aa("--name", type=str, metavar="name", default=None, help=instance.name)

# load instance
load = subparsers.add_parser("load", help="load a lamindb instance")
aa = load.add_argument
instance_help = """
The instance identifier can the instance name (owner is
current user), handle/name, or the URL: https://lamin.ai/handle/name."""
aa("instance", type=str, metavar="i", default=None, help=instance_help)
aa("--db", type=str, metavar="db", default=None, help=instance.db)
aa(
    "--storage",
    type=str,
    metavar="s",
    default=None,
    help="update storage while loading",
)

# delete instance
delete_parser = subparsers.add_parser("delete", help="delete instance")
aa = delete_parser.add_argument
aa("instance", type=str, metavar="i", default=None, help=instance.name)
aa = delete_parser.add_argument
aa("--force", default=False, action="store_true", help="Do not ask for confirmation")

# show instance info
info_parser = subparsers.add_parser("info", help="show user & instance info")

# set storage
set_storage_parser = subparsers.add_parser("set", help="update settings")
aa = set_storage_parser.add_argument
aa("--storage", type=str, metavar="f", help=instance.storage_root)

# close instance
subparsers.add_parser("close", help="close instance")

# register instance
subparsers.add_parser("register", help="register instance on hub")

# migrate
migr = subparsers.add_parser("migrate", help="manage migrations")
aa = migr.add_argument
aa(
    "action",
    choices=["create", "deploy", "squash"],
    help="Manage migrations.",
)
aa("--package-name", type=str, default=None)
aa("--end-number", type=str, default=None)
aa("--start-number", type=str, default=None)

# schema
schema_parser = subparsers.add_parser("schema", help="view schema")
aa = schema_parser.add_argument
aa(
    "action",
    choices=["view"],
    help="View schema.",
)

# track
track_parser = subparsers.add_parser("track", help="track notebook or script")
aa = track_parser.add_argument
filepath_help = "A path to the notebook."
aa("filepath", type=str, metavar="filepath", help=filepath_help)
pypackage_help = "One or more (delimited by ',') python packages to track."
aa("--pypackage", type=str, metavar="pypackage", default=None, help=pypackage_help)

# save
save_parser = subparsers.add_parser("save", help="save notebook or script")
aa = save_parser.add_argument
aa("filepath", type=str, metavar="filepath", help="filepath to notebook or script")

# stage
save_parser = subparsers.add_parser("stage", help="stage a notebook or script")
aa = save_parser.add_argument
aa("url", type=str, metavar="url", help="a lamin.ai url")

# login and logout user
login = subparsers.add_parser("login", help="log in")
aa = login.add_argument
aa("user", type=str, metavar="user", help="email or user handle")
aa("--key", type=str, metavar="k", default=None, help="API key")
aa("--password", type=str, metavar="pw", default=None, help="legacy password")
logout = subparsers.add_parser("logout", help="logout")

# manage cache
cache_parser = subparsers.add_parser("cache", help="manage cache")
cache_subparser = cache_parser.add_subparsers(dest="cache_action")
clear_parser = cache_subparser.add_parser("clear", help="Clear the cache directory.")
set_parser = cache_subparser.add_parser("set", help="Set the cache directory.")
aa = set_parser.add_argument
aa(
    "cache_dir",
    type=str,
    metavar="cache_dir",
    help="A new directory for the lamindb cache.",
)

# show version
try:
    lamindb_version = version("lamindb")
except PackageNotFoundError:
    lamindb_version = "lamindb installation not found"

parser.add_argument(
    "--version",
    action="version",
    version=lamindb_version,
    help="show lamindb version",
)


def main():
    args = parser.parse_args()

    from lamindb_setup._silence_loggers import silence_loggers

    silence_loggers()
    if args.command == "login":
        from lamindb_setup._setup_user import login

        return login(
            args.user,
            key=args.key,
            password=args.password,
        )
    elif args.command == "logout":
        from lamindb_setup._setup_user import logout

        return logout()
    elif args.command == "init":
        from lamindb_setup._init_instance import init

        return init(
            storage=args.storage,
            db=args.db,
            schema=args.schema,
            name=args.name,
        )
    elif args.command == "load":
        from lamindb_setup._load_instance import load

        return load(
            identifier=args.instance,
            db=args.db,
            storage=args.storage,
        )
    elif args.command == "close":
        from lamindb_setup._close import close

        return close()
    elif args.command == "register":
        from lamindb_setup._register_instance import register

        return register()
    elif args.command == "delete":
        from lamindb_setup._delete import delete

        return delete(args.instance, force=args.force)
    elif args.command == "info":
        from lamindb_setup._info import info

        return info()
    elif args.command == "set":
        from lamindb_setup._set import set

        return set.storage(args.storage)
    elif args.command == "migrate":
        from lamindb_setup._migrate import migrate

        if args.action == "create":
            return migrate.create()
        elif args.action == "deploy":
            return migrate.deploy()
        elif args.action == "squash":
            return migrate.squash(
                package_name=args.package_name,
                migration_nr=args.end_number,
                start_migration_nr=args.start_number,
            )
    elif args.command == "schema":
        from lamindb_setup._schema import view

        if args.action == "view":
            return view()
    elif args.command == "track":
        from lamin_cli._transform import track

        track(args.filepath, args.pypackage)
    elif args.command == "save":
        from lamin_cli._transform import save

        return save(args.filepath)
    elif args.command == "stage":
        from lamin_cli._stage import stage

        return stage(args.url)
    elif args.command == "cache":
        from lamindb_setup._cache import clear_cache_dir, get_cache_dir, set_cache_dir

        if args.cache_action == "set":
            set_cache_dir(args.cache_dir)
        elif args.cache_action == "clear":
            clear_cache_dir()
        else:
            print(f"The cache directory of the current instance is {get_cache_dir()}.")
    else:
        parser.print_help()
    return 0
