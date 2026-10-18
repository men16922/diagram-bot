"""boto3 sessions: the SDK credential chain, fixed temporary credentials, or credentials fetched on use. The only places a
session is built; aws_access.AwsAccess decides which one a user gets (and its network path), and the CLI and library
defaults use the same functions."""
import boto3
import botocore.session
from botocore.credentials import DeferredRefreshableCredentials


def sdk_session(region=None, profile=None):
    """The server's SDK chain: a named profile, else environment variables, .env, ~/.aws and instance roles."""
    return boto3.Session(profile_name=profile, region_name=region)


def credentials_session(credentials, region=None):
    """Fixed temporary credentials in STS's shape (AccessKeyId, SecretAccessKey, SessionToken) or botocore's
    refresh shape (access_key, secret_key, token; sso.SsoLogins.credentials)."""
    if "AccessKeyId" in credentials:
        credentials = {"access_key": credentials["AccessKeyId"], "secret_key": credentials["SecretAccessKey"],
                       "token": credentials["SessionToken"]}
    return boto3.Session(aws_access_key_id=credentials["access_key"], aws_secret_access_key=credentials["secret_key"],
                         aws_session_token=credentials["token"], region_name=region)


def refreshing_session(fetch, region):
    """A session whose credentials come from fetch() on first use and again before they expire (botocore refreshes),
    for a client that outlives one role session, such as the chat's Bedrock model. Nothing is fetched here."""
    core = botocore.session.Session()
    core._credentials = DeferredRefreshableCredentials(fetch, "diagram-bot-sso")
    return boto3.Session(botocore_session=core, region_name=region)
