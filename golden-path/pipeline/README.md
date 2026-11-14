# Delivery pipeline

Product repos do not copy these steps. They extend the template, so a fix to the gate reaches every team.

Azure Pipelines is the company front door (`azure-pipelines.yml`). This public repo runs the same gate on GitHub Actions, because that is where the submission lives and a reviewer can see it go green without an Azure DevOps project.

## What a team writes

```yaml
# azure-pipelines.yml in the product repo
trigger:
  branches:
    include: [main]

extends:
  template: golden-path/pipeline/azure-pipelines.yml@platform
  parameters:
    serviceDirectory: .
    imageRepository: account-balance
```

## What the template already does

1. **Build** — compile, then build the image from a pinned Dockerfile. The Dockerfile drops root.
2. **Test** — unit tests. Integration tests run against Postgres when `DATABASE_URL` is set.
3. **Scan** — dependency audit (`pip-audit`). Critical issues fail the build.
4. **Policy gate** — `scripts/policy_gate.py`. The build fails if the SLO spec is missing or stale, the runbook headings are missing, the telemetry standard is gutted, the image runs as root, or a cloud key is committed. The four cost-tag fields (`owner`, `service`, `environment`, `cost_centre`) have to be in the SLO spec. The Azure module described in DECISIONS.md enforces the same four on resources.
5. **Deploy** — only from `main`, and only after a person approves the `production` environment. This exercise does not push an image and does not change Azure. The approval is the point: the error-budget policy in DECISIONS.md is what that person is supposed to look at before they approve.

## What the gate deliberately does not do

It does not check a dashboard exists. It does not deploy. It does not decide that a feature is a good idea. It only stops a service reaching production without an SLO, a runbook, and the telemetry rules.
