# Workspaces and Members

A Cloudberry workspace is the container for your team's projects, boards, and
files. Every user belongs to at least one workspace, and data never crosses
between workspaces: members of one workspace cannot see the projects or files of
another. This isolation is enforced on the server for every request.

Workspaces have three member roles. Administrators manage billing, members, and
security settings. Editors can create and change projects but cannot touch
billing or remove people. Viewers have read-only access and are typically used
for clients or stakeholders who should see progress without changing anything.

To invite someone, an administrator opens Settings → Members and enters their
email. The invitee receives a link and joins with the role chosen at invite time;
the role can be changed later from the same page. Removing a member revokes their
access immediately and reassigns any projects they owned to the administrator who
removed them, so nothing is orphaned.