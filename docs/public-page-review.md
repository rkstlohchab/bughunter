# Public-page review

In **Chat**, ask to audit a website. Review the address, permission, safe paths and request limits in **Review and start**, then choose **Start task**. The app connects the gateway and returns the saved result in that conversation. Existing projects retain their recorded scope; changing it requires another review.

For manual controls, open **Tools (beside Stop all) → Open individual workspaces → Web testing → Start** and select a project. Start with the public entry page. Enable **Render the public start page and inspect its loaded assets** to collect a fresh Chromium snapshot as part of the audit.

The HTML crawler sends at most 30 page requests. Optional rendering permits at most 30 additional requests, serially scheduled at the project's rate. Both use the existing policy gateway, DNS checks, request budget and evidence store. Static resource paths must be declared; the audit never expands project scope itself. Redirect destinations are checked again. Query-bearing URLs, sensitive routes, POSTs and other origins are blocked before forwarding. Service workers, WebSockets and downloads are disabled. The snapshot does not load or save browser login state and never clicks or submits a form.

The report includes observed headers, forms without values, referenced resources, a screenshot, visible text, gateway evidence, blocked requests and static indicators from loaded assets. Asset indicators such as HTML insertion APIs, browser storage and source-map references are counts for manual review. They do not establish a vulnerable data flow. Source maps and endpoints mentioned in scripts are not probed.

Blocked API/payment/analytics dependencies may leave a JavaScript application partially rendered or blank. The report records partial coverage and any rendering error. Missing headers remain unconfirmed review leads. Authentication, access control, checkout integrity and exploitability require a separate defined test scope; this review does not establish their behavior.

Desktop RPC: `website.audit.start` accepts `project_id`, `approved`, `max_pages`, `seed_url` and optional `render_public`. `website.audit.report` exports the saved task. A stopped or uncertain request is never automatically replayed.
