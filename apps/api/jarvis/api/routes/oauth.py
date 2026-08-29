"""OAuth2 authorization server — authorization-code grant with PKCE.

Exists because Alexa account linking requires a real OAuth2 provider (blueprint §16).
Hosting one keeps a single identity system rather than bolting Cognito alongside.

The consent page is deliberately plain HTML with no JavaScript and no external assets:
it is rendered inside Alexa's linking webview, where anything else is a liability.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from jarvis.api.deps import IdentityDep
from jarvis.core.logging import get_logger
from jarvis.services.identity.service import OAuthError

log = get_logger(__name__)
router = APIRouter(prefix="/oauth", tags=["oauth"])


def _oauth_error_response(exc: OAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.error, "error_description": exc.description},
    )


@router.get("/authorize", response_class=HTMLResponse, response_model=None)
async def authorize_form(
    request: Request,
    identity: IdentityDep,
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    scope: str = "",
    state: str = "",
    code_challenge: str | None = None,
    code_challenge_method: str | None = "S256",
) -> HTMLResponse | JSONResponse:
    """Render the sign-in form after validating the request.

    Validation happens *before* rendering so an invalid client or unregistered
    redirect_uri never reaches a page that collects a password.
    """
    try:
        client = await identity.validate_authorization_request(
            client_id=client_id,
            redirect_uri=redirect_uri,
            response_type=response_type,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )
    except OAuthError as exc:
        return _oauth_error_response(exc)

    return HTMLResponse(
        _consent_page(
            client_name=client.name,
            scope=scope,
            hidden={
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": response_type,
                "scope": scope,
                "state": state,
                "code_challenge": code_challenge or "",
                "code_challenge_method": code_challenge_method or "",
            },
            error=request.query_params.get("error"),
        )
    )


@router.post("/authorize", response_model=None)
async def authorize_submit(
    identity: IdentityDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    redirect_uri: Annotated[str, Form()],
    response_type: Annotated[str, Form()] = "code",
    scope: Annotated[str, Form()] = "",
    state: Annotated[str, Form()] = "",
    code_challenge: Annotated[str, Form()] = "",
    code_challenge_method: Annotated[str, Form()] = "S256",
):
    try:
        client = await identity.validate_authorization_request(
            client_id=client_id,
            redirect_uri=redirect_uri,
            response_type=response_type,
            code_challenge=code_challenge or None,
            code_challenge_method=code_challenge_method or None,
        )
    except OAuthError as exc:
        return _oauth_error_response(exc)

    try:
        user = await identity.authenticate(email, password)
    except Exception:  # noqa: BLE001
        # Back to the form. The failure never reaches the redirect_uri, so a wrong
        # password cannot be probed from outside the consent page.
        params = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": response_type,
                "scope": scope,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "error": "Invalid email or password",
            }
        )
        return RedirectResponse(f"/oauth/authorize?{params}", status_code=303)

    code = await identity.issue_authorization_code(
        user=user,
        client=client,
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge or None,
        code_challenge_method=code_challenge_method or None,
    )

    query = {"code": code}
    if state:
        query["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{separator}{urlencode(query)}", status_code=303)


@router.post("/token", response_model=None)
async def token(
    identity: IdentityDep,
    grant_type: Annotated[str, Form()],
    code: Annotated[str, Form()] = "",
    redirect_uri: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
    code_verifier: Annotated[str, Form()] = "",
    refresh_token: Annotated[str, Form()] = "",
):
    try:
        if grant_type == "authorization_code":
            access, refresh, expires_in, granted_scope = await identity.exchange_code(
                code=code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier or None,
                client_secret=client_secret or None,
            )
            body = {
                "access_token": access,
                "refresh_token": refresh,
                "token_type": "Bearer",
                "expires_in": expires_in,
            }
            if granted_scope:
                body["scope"] = granted_scope
            return JSONResponse(body)

        if grant_type == "refresh_token":
            access, refresh, expires_in = await identity.refresh_session(refresh_token)
            return JSONResponse(
                {
                    "access_token": access,
                    "refresh_token": refresh,
                    "token_type": "Bearer",
                    "expires_in": expires_in,
                }
            )
    except OAuthError as exc:
        return _oauth_error_response(exc)
    except Exception as exc:  # noqa: BLE001
        log.info("oauth_token_rejected", grant_type=grant_type, reason=type(exc).__name__)
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Token request rejected"},
        )

    return JSONResponse(
        status_code=400,
        content={
            "error": "unsupported_grant_type",
            "error_description": f"{grant_type} is not supported",
        },
    )


@router.post("/revoke")
async def revoke(identity: IdentityDep, token: Annotated[str, Form()]) -> JSONResponse:
    """RFC 7009. Always 200: revealing whether a token existed is an oracle."""
    await identity.revoke_refresh_token(token)
    return JSONResponse({"revoked": True})


def _consent_page(
    *, client_name: str, scope: str, hidden: dict[str, str], error: str | None
) -> str:
    from html import escape

    fields = "\n".join(
        f'<input type="hidden" name="{escape(k)}" value="{escape(v)}">'
        for k, v in hidden.items()
    )
    scopes = (
        "<ul>" + "".join(f"<li>{escape(s)}</li>" for s in scope.split()) + "</ul>"
        if scope
        else "<p>Basic account access.</p>"
    )
    banner = (
        f'<p role="alert" class="err">{escape(error)}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link {escape(client_name)} to JARVIS X</title>
<style>
 body{{font:16px/1.5 system-ui,sans-serif;margin:0;background:#0f1621;color:#e6edf3;
      display:grid;place-items:center;min-height:100vh;padding:1rem}}
 .card{{background:#161f2c;padding:2rem;border-radius:12px;max-width:26rem;width:100%}}
 h1{{font-size:1.25rem;margin:0 0 .25rem}} p.sub{{color:#8b98a9;margin:0 0 1.5rem}}
 label{{display:block;margin:.75rem 0 .25rem;font-size:.875rem;color:#8b98a9}}
 input[type=email],input[type=password]{{width:100%;padding:.6rem;border-radius:6px;
      border:1px solid #2b3746;background:#0f1621;color:#e6edf3;box-sizing:border-box}}
 button{{margin-top:1.25rem;width:100%;padding:.7rem;border:0;border-radius:6px;
      background:#2f81f7;color:#fff;font-weight:600;cursor:pointer}}
 .err{{background:#3d1d1d;border:1px solid #6b2b2b;padding:.6rem;border-radius:6px;
      font-size:.875rem}}
 ul{{margin:.25rem 0 0 1rem;padding:0;color:#8b98a9;font-size:.875rem}}
</style></head><body>
<form class="card" method="post" action="/oauth/authorize">
  <h1>Link {escape(client_name)}</h1>
  <p class="sub">Sign in to grant access to your JARVIS X account.</p>
  {banner}
  <p style="font-size:.875rem;color:#8b98a9">This will allow:</p>
  {scopes}
  <label for="email">Email</label>
  <input id="email" type="email" name="email" autocomplete="username" required>
  <label for="password">Password</label>
  <input id="password" type="password" name="password" autocomplete="current-password" required>
  {fields}
  <button type="submit">Sign in and link</button>
</form></body></html>"""
