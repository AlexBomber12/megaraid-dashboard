# Read-only production smoke check

> Verification record: the GET-only UI check for #136 found no browser errors or
> horizontal overflow in the checked views and confirmed the three status fixes.
> The #137 post-deploy check used service health, persisted date and current-process
> journal; it was not a fresh full browser audit. #139–141 subsequently received
> production GET-only UI checks, including both Observed refresh paths. See [CURRENT_STATE.md](CURRENT_STATE.md).
> This manual procedure remains read-only even when production has action capability.

This is a manual procedure for `https://megaraid.alexbomber.com/`, not a scheduled
job or deployment step. It has not been run by the automated safety suite. Use an
already authorized account. Do not put credentials in commands, URLs, screenshots,
HAR files, shell history, CI output or saved browser traces. Disable network
recording/export before authenticating. Report only paths, response status, TLS
validation outcome, application revision and approximate data freshness.

1. Open the exact HTTPS origin in a private browser window. Verify the certificate
   is trusted, hostname matches, and no TLS warning occurs. Do not bypass a warning.
2. Before login, verify a protected page presents the Basic authentication challenge.
   Submit the valid credentials through the browser prompt. Do not deliberately
   repeat invalid passwords against production or trigger rate limits.
3. Inspect DevTools Network without exporting it. Only GET requests are permitted.
   For a stronger local guard, use a read-only browser proxy that blocks all methods
   except GET, configured entirely on the operator workstation. Do not change nginx,
   the application or any server setting. Stop if a navigation tries to POST.
4. Visit `/`, `/controller`, `/drives`, `/events`, and `/audit` through navigation.
   Follow the audit redirect, one drive tile, its breadcrumb, Previous/Next drive,
   and a backplane slot. Verify successful pages, styles/icons/scripts, readable
   data, selected navigation and absence of browser errors.
5. Compare the displayed controller model, drive count/slots and snapshot timestamp
   with the expected host and current time. Wait for one normal refresh or use
   Refresh now (GET only). Check that the snapshot age is plausible; report stale,
   unknown or unavailable data without attempting a repair.
6. Exercise only GET/local controls: table sorting, history range tabs, event filter
   links and Load Older Events if present. Verify results and no POST requests.
   Do not visit replacement rebuild-status endpoints: those GET handlers can record
   rebuild audit events. Do not open replacement workflows or foreign actions.
7. Open `/health` and `/healthz` with GET. Expect the application version and health
   JSON; record database/collector status without probing StorCLI from a shell.
8. Close the private window. Report observed paths/statuses, TLS and freshness.
   Physical LEDs, motors, buzzer, rebuilds and foreign metadata remain unverified.

Never click Start/Stop Maintenance, Locate, buzzer controls, Patrol Read, Consistency
Check, Spin Down, Hot Spare, UGood/UBad, replacement actions or foreign import/clear
on this smoke check. Do not POST even a dry run. Do not create synthetic incidents,
send test notifications, invoke StorCLI/sudo, change configuration, install tools
on the server, deploy code or create a schedule. If something fails, record the
read-only observation and investigate through a separate authorized task.
