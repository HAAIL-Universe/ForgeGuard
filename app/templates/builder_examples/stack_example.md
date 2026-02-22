# [PROJECT NAME] — Technology Stack

⚠ FORMAT SPECIFICATION ONLY.
ALL technology choices MUST come from the canonical anchor and questionnaire answers.
Do NOT use any technology names from this skeleton — derive them from the user's answers.

## Backend

- **Language:** [from canonical anchor]
- **Framework:** [from canonical anchor]
- **Key libraries:** [as required by the project]

## Database

- **Engine:** [from canonical anchor]
- **Version:** [latest stable for the chosen engine]
- **Connection:** [connection pool or ORM method]

## Auth

- **Method:** [from canonical anchor — e.g. JWT, OAuth, session]
- **Provider:** [if applicable]
- **Flow:** [description of the auth flow]

## Frontend

- **Framework:** [from canonical anchor]
- **Bundler:** [as appropriate for the framework]
- **Key libraries:** [as required]

## Testing

- **Framework:** [appropriate for the backend language]
- **Coverage target:** [%]

## Deployment

- **Platform:** [from canonical anchor]
- **Local dev:** [Docker Compose or equivalent]
- **Expected scale:** [one line]

## Environment Variables

| Name | Description | Required |
|------|-------------|----------|
| [VAR_NAME] | [what this variable controls] | Yes/No |
| [VAR_NAME] | [description] | Yes/No |

## forge.json

```json
{
  "boot_script": "[path/to/startup/script]",
  "build_mode": "full"
}
```
