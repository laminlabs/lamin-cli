import os
from datetime import datetime, timedelta, timezone

from lamindb_setup import settings
from lamindb_setup.core._hub_client import connect_hub_with_auth
from lamindb_setup.core._hub_core import create_api_key


def test_entrypoint():
    exit_status = os.system("lamin --help")
    assert exit_status == 0


def test_cli_login():
    exit_status = os.system("lamin login testuser1")
    assert exit_status == 0
    assert settings.user.handle == "testuser1"


def test_cli_login_api_key():
    settings._user_settings = None  # this is to refresh a settings instance
    assert settings.user.handle == "testuser1"

    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=1)
    api_key = create_api_key(
        {
            "expires_at": expires_at.strftime("%Y-%m-%d"),
            "description": "test_cli_login_api_key",
        }
    )

    exit_status = os.system(f"echo {api_key} | lamin login")
    assert exit_status == 0

    os.environ["LAMIN_API_KEY"] = api_key
    exit_status = os.system("lamin login")
    assert exit_status == 0

    settings._user_settings = None
    assert settings.user.handle == "testuser1"
    assert settings.user.api_key == api_key

    hub = connect_hub_with_auth()
    hub.table("api_key").delete().eq("description", "test_cli_login_api_key").execute()
    hub.auth.sign_out({"scope": "local"})
