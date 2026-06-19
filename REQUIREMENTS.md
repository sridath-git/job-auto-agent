# Job Auto Agent Requirements

## Project Goal

Job Auto Agent is an AI-powered job discovery, matching, and application assistant optimized for Sridath Jeelugula's professional background.

The system should read Gmail job alerts and recruiter emails, extract job opportunities, score them against Sridath's profile, prioritize the most relevant opportunities, and eventually help generate customized application materials.

The system should be designed to maximize interview opportunities for roles matching Sridath's experience.

## Candidate Profile

Name: Sridath Jeelugula

Location: Montreal, Quebec, Canada

Experience: 7+ years across:

- Site Reliability Engineering (SRE)
- DevOps
- DevSecOps
- Platform Engineering
- Cloud Engineering
- Cloud Security
- Application Security
- Kubernetes Engineering
- PKI Engineering
- Identity and Access Management
- Reliability Engineering

## Cloud Platforms

- Azure
- AWS
- Hybrid Cloud

## Primary Skills

- Kubernetes
- AKS
- EKS
- OpenShift
- Terraform
- Ansible
- Jenkins
- GitHub Actions
- Azure DevOps
- Helm
- FluxCD
- ArgoCD
- Istio
- Datadog
- Prometheus
- Grafana
- PagerDuty
- ELK
- Filebeat
- HashiCorp Vault
- PKI
- Certificate Management
- Intermediate CA Administration
- X.509 Certificates
- cert-manager
- OIDC
- RBAC
- mTLS
- Azure AD
- CyberArk

## Security Skills

- DevSecOps
- Application Security
- SAST
- DAST
- SCA
- Veracode
- SonarQube
- Invicti
- GitHub Advanced Security
- Container Security
- Vulnerability Management
- Secret Detection
- Secret Remediation
- Software Supply Chain Security
- MLSecOps

## Reliability Skills

- SRE
- Reliability Engineering
- Incident Response
- Root Cause Analysis
- Error Budgets
- SLO
- SLA
- Observability
- Monitoring
- On-call Operations
- Production Support

## Target Job Titles

Highest-priority job titles:

- Site Reliability Engineer
- Senior Site Reliability Engineer
- Staff Site Reliability Engineer
- DevOps Engineer
- Senior DevOps Engineer
- Cloud Engineer
- Senior Cloud Engineer
- Cloud Platform Engineer
- Platform Engineer
- Senior Platform Engineer
- Infrastructure Engineer
- Platform Reliability Engineer
- Kubernetes Engineer
- DevSecOps Engineer
- Senior DevSecOps Engineer
- Cloud Security Engineer
- Application Security Engineer
- Platform Security Engineer
- PKI Engineer
- Senior PKI Engineer
- Vault Engineer
- Identity Engineer

## Match-Boosting Keywords

The following terms must increase match score.

### Broad Industry Terms

- DevOps
- CI/CD
- Platform Engineering
- Cloud Engineering
- Infrastructure
- Automation
- Reliability
- SRE
- Observability
- Monitoring
- Kubernetes
- Cloud Security
- DevSecOps
- Infrastructure as Code
- Incident Management
- Production Support

### Technology Terms

- Azure
- AWS
- Kubernetes
- AKS
- EKS
- OpenShift
- Terraform
- Ansible
- Jenkins
- GitHub Actions
- Azure DevOps
- Helm
- FluxCD
- ArgoCD
- Istio
- Datadog
- Prometheus
- Grafana
- PagerDuty
- ELK
- Vault
- HashiCorp Vault
- PKI
- Certificate Management
- cert-manager
- OIDC
- RBAC
- mTLS
- CyberArk
- Veracode
- SonarQube
- Invicti
- SAST
- DAST
- SCA
- Container Security
- Vulnerability Management
- MLSecOps

## Jobs to Deprioritize

The following roles should receive lower match scores unless the job description strongly overlaps with the candidate profile:

- Java Developer
- Full Stack Developer
- Frontend Developer
- Backend Developer
- QA Tester
- QA Analyst
- Manual Tester
- Business Analyst
- Data Analyst
- Product Owner
- Scrum Master
- Project Manager
- Salesforce Developer
- SAP Consultant

## MVP Requirements

1. Read Gmail job-alert emails.
2. Read recruiter emails.
3. Read company career-alert emails.
4. Extract jobs from LinkedIn, Indeed, Dice, Glassdoor, Workday, Greenhouse, Lever, and company career portals.
5. Store jobs in SQLite.
6. Avoid duplicate jobs.
7. Score jobs against the candidate profile.
8. Display jobs in a Streamlit dashboard.
9. Allow filtering by match score, company, source, location, date, work mode, and status.
10. Allow job statuses: New, Interested, Rejected, Applied, and Follow-up.
11. Maintain job history.
12. Maintain application history.

## Job Fields to Extract

- Job Title
- Company
- Location
- Country
- Remote/Hybrid/On-site
- Employment Type
- Salary
- Job URL
- Source
- Description
- Required Skills
- Posted Date
- Email Subject
- Email Received Date

## Scoring Requirements

The scoring engine should not rely only on exact keyword matching. It should recognize related skills, synonymous terms, and adjacent concepts.

Expected scoring behavior:

- SRE jobs should score highly.
- DevOps jobs should score highly.
- Platform Engineering jobs should score highly.
- Kubernetes jobs should score highly.
- Cloud Engineering jobs should score highly.
- Vault and PKI jobs should score extremely highly.
- DevSecOps jobs should score extremely highly.

## Security Requirements

- Use Gmail read-only access only.
- Never store Gmail passwords.
- Never commit `credentials.json`.
- Never commit `token.json`.
- Never commit `.env`.
- Never commit API keys.
- Never auto-send emails.
- Never auto-submit applications without explicit approval.

## Future Roadmap

### Phase 2

- Resume ingestion
- Resume tailoring
- Skill-gap analysis
- Job-specific resume generation

### Phase 3

- Cover letter generation
- Recruiter email reply drafts
- Interview preparation notes

### Phase 4

- Telegram notifications
- Email notifications
- Daily job summaries

### Phase 5

- Workday automation
- Greenhouse automation
- Lever automation

### Phase 6

- Auto-apply only after explicit manual approval
