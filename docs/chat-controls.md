# Chat, permissions and stopping

Start in Chat, or use **Tools** beside **Stop all** to choose a task. The guide places a starter message in Chat; it does not start work itself. **Open individual workspaces** retains the existing tools and adds a search box.

For a public website review, Chat collects the target, read-only paths, permission and page limits before starting. It connects the gateway and saves the result in the conversation. Local app checks and knowledge checks also report there. Work beyond the reviewed scope still requires review. Mac control keeps its selected-app permission session, and memory operations retain their existing operation checks.

**Stop all** is in the top bar on Chat, Library, Settings and the manual workspaces. It latches before requesting cancellation of app-managed tasks, model/tool loops, imports, setup jobs and native sessions. A request already sent cannot be recalled, and an in-flight network/OS operation may finish. Cancellation failures are reported. **Resume tasks** enables new tasks; it does not restart stopped work. Independently launched applications and services remain under their own controls.

Settings no longer has a duplicate **Task controls** section. Opening **Automatic setup** only checks status. **Set up tools** starts optional setup.

Mac permission status comes from the running native process and refreshes when Settings regains focus. Screen Recording and Accessibility use macOS preflight APIs. Full Disk Access uses open-only probes of the user and system permission-database paths; it never reads database contents. Unsuccessful Full Disk Access probes show **Check in macOS Settings**; they do not establish a denial. Other native permissions distinguish an unavailable check from a denial. Browser previews cannot determine native grants. If a protected file remains inaccessible after enabling the app, relaunch it. The process-path dropdown has been removed.

Apple recommends checking the actual access required by the task rather than relying on a universal Full Disk Access flag: [Apple Developer Forums](https://developer.apple.com/forums/thread/835851).
