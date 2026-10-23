# Workflow Modernization Agent: scorecard

LLM provider for steps without a rule: `fixture`

| Metric | Value |
|---|---|
| Sample workflows | 12 |
| Steps (actions + control flow) | 83 (65 actions) |
| Actions converted automatically | 62 of 65 (95%): 53 by rule, 9 by bounded LLM |
| Actions flagged for manual rebuild | 3 |
| Workflows converted with no flagged step | 9 of 12 |
| Behavioral test cases passed | 24 of 29 (83%) |
| Workflows behavior-equivalent on every case | 9 of 12 |
| Failing cases without a flagged step (silent failures) | 0 |
| Static issues in generated flows | 0 |
| Estimated review effort | 269 min vs 766 min manual rebuild (65% less) |

Review-effort figures use the heuristic weights in `wma/evaluate.py`; they are estimates, not measurements.

## Per workflow

| Workflow | Source | Steps | Rule | LLM | Flagged | Cases passed | Equivalent |
|---|---|---|---|---|---|---|---|
| clinical-document-review | clinical_document_review.json | 6 | 4 | 1 | 0 | 2/2 | yes |
| contract-renewal | contract_renewal.xml | 5 | 3 | 2 | 0 | 1/1 | yes |
| customer-complaint | customer_complaint.json | 6 | 3 | 1 | 1 | 1/3 | no |
| document-retention | document_retention.json | 5 | 3 | 0 | 0 | 2/2 | yes |
| employee-onboarding | employee_onboarding.xml | 6 | 3 | 2 | 0 | 2/2 | yes |
| expense-escalation | expense_escalation.xml | 7 | 5 | 0 | 0 | 3/3 | yes |
| incident-notification | incident_notification.xml | 6 | 4 | 1 | 0 | 2/2 | yes |
| invoice-approval | invoice_approval.xml | 11 | 8 | 1 | 0 | 3/3 | yes |
| it-access-request | it_access_request.json | 6 | 4 | 0 | 1 | 1/2 | no |
| kyc-review | kyc_review.xml | 11 | 6 | 1 | 1 | 2/4 | no |
| leave-request | leave_request.json | 5 | 4 | 0 | 0 | 2/2 | yes |
| purchase-order | purchase_order.json | 9 | 6 | 0 | 0 | 3/3 | yes |

## Flagged steps and differences

- **customer-complaint / s4 (SendSMS)**: confidence 0.55 is below 0.70
  - case *high severity*: event 2: source={'kind': 'sms', 'phone': '+44 7700 900123', 'message': 'Urgent complaint CMP-501'} flow={'kind': 'sp_update', 'list': 'Complaints', 'item_id': 501, 'fields': {'Priority': 'Urgent'}}
  - case *platinum customer*: event 2: source={'kind': 'sms', 'phone': '+44 7700 900123', 'message': 'Urgent complaint CMP-503'} flow={'kind': 'sp_update', 'list': 'Complaints', 'item_id': 503, 'fields': {'Priority': 'Urgent'}}
- **it-access-request / s3 (RunPowerShellScript)**: model reports no equivalent: Running PowerShell against Active Directory has no catalog action; it needs an Azure Automation runbook or an Entra ID group connector.
  - case *approved*: event 2: source={'kind': 'script', 'script': 'Add-ADGroupMember -Identity SAP-GRC-Users -Members ana@contoso.com'} flow={'kind': 'email', 'to': 'ana@contoso.com', 'subject': 'Access granted: SAP GRC', 'body': 'You now have access to SAP GRC.'}
- **kyc-review / s7 (AssignToGroup)**: proposed target 'sharepoint.set_permissions' is outside the catalog
  - case *high risk cleared*: event 3: source={'kind': 'permissions', 'group': 'Financial Crime Unit', 'item_id': 33} flow={'kind': 'sp_update', 'list': 'KYC Cases', 'item_id': 33, 'fields': {'RiskBand': 'High'}}
  - case *high risk rejected*: event 3: source={'kind': 'permissions', 'group': 'Financial Crime Unit', 'item_id': 34} flow={'kind': 'sp_update', 'list': 'KYC Cases', 'item_id': 34, 'fields': {'RiskBand': 'High'}}

## Steps mapped by the bounded LLM

- clinical-document-review / s6 (CreateTask) -> `planner.create_task` (confidence 0.88): Revision task maps to Planner Create a task.
- contract-renewal / s3 (PostToTeamsChannel) -> `teams.post_message` (confidence 0.93): Channel post maps to Teams.
- contract-renewal / s5 (LogToHistoryList) -> `sharepoint.create_item` (confidence 0.82): History entries are list items in the workflow history list.
- customer-complaint / s3 (PostToTeamsChannel) -> `teams.post_message` (confidence 0.92): Channel post maps to Teams.
- employee-onboarding / s2 (CreateTask) -> `planner.create_task` (confidence 0.88): A task with plan, title and assignee maps to Planner Create a task.
- employee-onboarding / s3 (PostToTeamsChannel) -> `teams.post_message` (confidence 0.93): Channel post maps to Teams Post message in a chat or channel.
- incident-notification / s3 (PostToTeamsChannel) -> `teams.post_message` (confidence 0.93): Channel post maps to Teams.
- invoice-approval / s11 (LogToHistoryList) -> `sharepoint.create_item` (confidence 0.82): LogToHistoryList writes an item to the workflow history list; Create item on that list reproduces it.
- kyc-review / s11 (LogToHistoryList) -> `sharepoint.create_item` (confidence 0.82): History entries are list items in the workflow history list.
