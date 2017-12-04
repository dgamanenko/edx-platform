
import os
import json

from path import path

SERVICE_VARIANT = os.environ.get('SERVICE_VARIANT', None)
CONFIG_ROOT = path('/edx/app/edxapp/')  #don't hardcode this in the future
CONFIG_PREFIX = SERVICE_VARIANT + "." if SERVICE_VARIANT else ""
with open(CONFIG_ROOT / CONFIG_PREFIX + 'env.json') as env_file:
    ENV_TOKENS = json.load(env_file)

APPSEMBLER_FEATURES = ENV_TOKENS.get('APPSEMBLER_FEATURES', {})

# search APPSEMBLER_FEATURES first, env variables second, fallback to None
GOOGLE_TAG_MANAGER_ID = APPSEMBLER_FEATURES.get('GOOGLE_TAG_MANAGER_ID', os.environ.get('GOOGLE_TAG_MANAGER_ID', None))

INTERCOM_APP_ID = APPSEMBLER_FEATURES.get('INTERCOM_APP_ID', os.environ.get('INTERCOM_APP_ID', ''))
INTERCOM_API_KEY = APPSEMBLER_FEATURES.get('INTERCOM_API_KEY', os.environ.get('INTERCOM_API_KEY', ''))
INTERCOM_USER_EMAIL = APPSEMBLER_FEATURES.get('INTERCOM_USER_EMAIL', os.environ.get('INTERCOM_USER_EMAIL', ''))


# We keep  'APPSEMBLER_REPORTING' until after we've deployed edx-figure to 
# production and proven it
if APPSEMBLER_FEATURES.get('ENABLE_APPSEMBLER_REPORTING', False):
    from appsembler_reporting.settings import APPSEMBLER_REPORTING

    # This will just merge and update top level keys/values
    APPSEMBLER_REPORTING.update(APPSEMBLER_FEATURES.get(
        'APPSEMBLER_REPORTING', {} ))

# Monkeypatch edx_figures settings from env/json
if ENV_TOKENS.get('EDX_FIGURES', {}).get('ENABLED', False):
    try:
        from edx_figures.settings import EDX_FIGURES

        # This will just merge and update top level keys/values
        EDX_FIGURES.update(ENV_TOKENS.get('EDX_FIGURES',{}))
    except ImportError:
        pass
