# Security Policy

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, email [kent@cherry.ai](mailto:kent@cherry.ai) with a detailed report.
We will acknowledge your report within 48 hours and aim to provide a fix or
mitigation plan within 7 days.

Include as much of the following information as possible:

- A description of the vulnerability and its impact
- Steps to reproduce or a proof-of-concept
- The affected version range
- Any possible mitigations you've identified

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

We will backport security fixes to the latest minor release in each supported
major version line.

## Disclosure Process

1. Reporter submits vulnerability via email
2. Maintainers acknowledge receipt within 48 hours
3. Maintainers validate and assess severity
4. A fix is developed and tested privately
5. A release is prepared with the fix
6. The vulnerability is disclosed in the release notes after the fix is
   published

We follow [responsible disclosure](https://en.wikipedia.org/wiki/Responsible_disclosure)
principles. We ask that you give us reasonable time to investigate and address
the issue before making any public disclosure.

## Security Best Practices

When using this SDK:

- **Never commit credentials** to version control. Use environment variables or
  the Fastmail CLI config file for app passwords and API tokens.
- **Use app passwords** for CalDAV/CardDAV — never your Fastmail account
  password.
- **Rotate API tokens regularly** via Fastmail Settings > Privacy & Security >
  API tokens.
- **Use read-only tokens** when you only need to read data.