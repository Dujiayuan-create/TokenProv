from dataclasses import replace
from tokenprov.common.models import Authorization
from tokenprov.crypto.signing import sign_object, verify_object


def create_authorization(auth: Authorization, private_key: str) -> Authorization:
    return replace(auth, user_signature=sign_object(private_key, "authorization/v1", auth.signed_payload()))


def verify_authorization(auth: Authorization, public_key: str) -> bool:
    return verify_object(public_key, "authorization/v1", auth.signed_payload(), auth.user_signature)

