# Getting Started — Authentication

The Lawstronaut API uses a custom OAuth 2.0 flow. Authentication happens against the
`filerskeepersapi.co` host; subsequent API calls hit `api.lawstronaut.com/v2`.

## 1. Log in

```http
POST https://filerskeepersapi.co/auth/login
Content-Type: application/json

{"email": "your_email", "password": "your_password"}
```

Response:

```json
{
  "data": {
    "token": {
      "refresh_token": "REFRESH_TOKEN",
      "token_type": "bearer",
      "expires_in": 1800
    }
  }
}
```

**Notes**

- The field is called `refresh_token`, but it is also the value passed as the bearer token on API requests. (The naming is unusual — there is no separate "access token" in the documented flow.)
- `expires_in` is in **seconds**. `1800` = 30 minutes.

## 2. Call the API

Include the token as a bearer in the `Authorization` header. `Accept: application/json` is required.

```http
GET https://api.lawstronaut.com/v2/jurisdictions
Accept: application/json
Authorization: Bearer REFRESH_TOKEN
```

## 3. Refresh on 401

Once the token expires, the API returns `401 Unauthorized`. Exchange the old token for a new one:

```http
POST https://filerskeepersapi.co/auth/refresh-token
Content-Type: application/json

{"token": "REFRESH_TOKEN"}
```

(The portal docs show a stray double slash — `filerskeepersapi.co//auth/refresh-token` — treat as a typo.)

## HTTP status codes

| Code | Meaning |
|------|---------|
| 200  | OK |
| 400  | Bad Request — invalid query/URI parameter, or invalid JSON |
| 401  | Unauthorized — missing/invalid/expired token |
| 404  | Not Found — endpoint URL does not exist (also returned by `/content/{id}/file-url` when no file exists) |

## Implementation hints for the watcher tool

- Keep credentials in env (`LAWSTRONAUT_EMAIL`, `LAWSTRONAUT_PASSWORD`) or a secrets manager.
- Cache the token + expiry; refresh proactively a few minutes before `expires_in` elapses to avoid mid-job 401s.
- On 401, refresh once and retry the request; if the refresh itself returns 401, re-login.
- Treat the bearer token as a secret — never log it.
