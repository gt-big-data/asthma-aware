# Authentication and symptom logging

Use Node 22.13 or newer (see `.nvmrc`). From `Front-end/`:

```powershell
npm install
npm run typecheck
npm run lint
npm test
npm run web
```

Copy `.env.example` to ignored `.env.local`. Configure the Supabase project URL
and **publishable** key. Never place database URIs, operator credentials,
service-role keys, or test-user passwords in `EXPO_PUBLIC_*` variables.

Run FastAPI from `backend/` with its configured runtime database login. The app
defaults to port 8000 on loopback for web/iOS simulator and `10.0.2.2` for the
Android emulator. Physical devices need `EXPO_PUBLIC_API_URL` set to the machine's
reachable LAN address or a deployed API. Allow the actual web origin in backend
`CORS_ORIGINS`. The current local web preview uses port 8082 because 8081 was occupied.

## Flow

- The Symptom Log tab offers email/password sign-in and account creation.
- Email confirmation follows the Supabase project's policy. After confirming,
  return to the app and sign in. OAuth, password recovery, and deep-link callbacks
  are not part of this slice.
- Sessions persist through the Supabase client, with native AsyncStorage and
  foreground token refresh. API requests obtain the current access token;
  FastAPI verifies it before accessing private user records.
- Dates follow the account timezone. A device-timezone option appears when they
  differ. Changing timezone affects new entries, not existing log boundaries.
- Users can log today or the previous five calendar dates. Selection remains
  anchored when midnight passes, so unsaved values are not discarded.
- Current location is obtained on request with foreground permission only.
  Users can select a map point instead, or enter coordinates if the map fails.
  No default Atlanta point is silently saved as the user's location.
- Saves send the last known revision. Identical retries are safe; conflicts
  require explicit reload. Network failures retain the unsaved form values.
- Switching dates asks before discarding changes. Signing out requires saving
  or discarding pending changes, and clears the displayed account/log state.

The dashboard and risk map still show their existing demo content. This change
does not connect them to live forecasts or claim personal model fitting is running.

## Verification

TypeScript, lint, date/API-client tests, and web export are checked. The tests
cover timezone/DST date ranges, coordinate validation, refreshed tokens, absent
entries versus service failures, optimistic revisions, and uncertain save timeouts.

A dedicated development user was used for real Supabase password sign-in and
backend JWT/profile/logging checks. The database transaction was rolled back and
the test session signed out. Credentials were loaded from ignored local files
without printing them. Browser layout checks cover the signed-out screen;
native device permissions and GPS still require device testing.

The existing dependency tree reports npm audit findings (30 at installation).
This slice did not apply broad or forced dependency upgrades; review those
findings separately before deployment.

For a manual check, sign in on the Symptom Log tab, choose a severity and a map
location, save, revisit the date, edit, and sign out. Only use synthetic health
data while validating the development environment.
