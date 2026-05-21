# NPDC / AWS — agent EC2 credential provisioning

Two scripts, both run from an admin account:

- **`create-ec2-role`** — provisions the least-privilege assumable role
  and emits the `~/.aws` config the agent needs.
- **`import-ssh-key`** — installs the agent's SSH *public* key as an EC2
  key pair so launched instances are SSH-reachable.

## Common flag convention

Both scripts honor the standard AWS CLI **`--profile=P`**, passed straight
through to `aws` — it selects *your admin credentials* doing the work. Omit
it to use the default profile / `AWS_PROFILE` / env vars.

`create-ec2-role` additionally takes **`--name=NAME`** (default `claude`)
— the *agent identity* that drives the IAM resource names, the `owner` tag,
and the `[profile NAME]` block in the emitted config. (Two different
profiles: `--name` names the *limited agent* profile that ends up in the
agent's config; `--profile` is *your admin* profile that creates it.)
`import-ssh-key` has no `--name`; it names the key pair from `--key-name`
or the key's comment.

---

## `create-ec2-role`

Provisions a least-privilege, **assumable** role that lets an autonomous
agent (Claude) launch and manage *only its own* EC2 instances, and emits the
`~/.aws` config the agent needs.

## Run it (as an account admin)

```bash
./create-ec2-role                       # name defaults to "claude", default AWS creds
./create-ec2-role --profile=vae-admin   # use a specific admin profile
./create-ec2-role --new-key             # rotate the base user's key
./create-ec2-role --save                # also write the snippets to ./claude.{config,credentials}
./create-ec2-role --delete              # tear it all down
```

By default the `[profile NAME]` and `[NAME-base]` blocks are **printed to
stdout only**. Pass `--save` (writes to the current dir) or `--save=DIR` to
also drop them in files (`NAME.config`, `NAME.credentials` mode 600).

The account ID is discovered at runtime (`sts get-caller-identity`); it is
not written into the script. `--region` only sets the CLI default in the
emitted config — it is **not** an IAM restriction (region and instance type
are intentionally unrestricted).

## What gets created (for `--name=NAME`)

| Resource | Name | Purpose |
|----------|------|---------|
| Policy | `NAME-ec2-owner` | EC2 ABAC: launch instances tagged `owner=NAME`; start/stop/reboot/terminate/modify only `owner=NAME` instances; unconditional `Describe*`. |
| User | `NAME-base` | Assume-only. Its sole permission is `sts:AssumeRole` on the role. A leaked key can do nothing but mint a time-boxed EC2 session. |
| Role | `NAME-ec2` | Carries the policy; trusts `NAME-base`; 12 h max session. |

## How the agent uses it

The script emits two snippets:

- the `[profile NAME]` block → append to the agent's `~/.aws/config`
- the `[NAME-base]` block → append to the agent's `~/.aws/credentials`

After that the role assumption is invisible — the agent just adds
`--profile NAME`. The CLI sees `role_arn` + `source_profile`, calls
`AssumeRole` with the base key, caches the temporary session, and refreshes
it automatically:

```bash
aws --profile NAME ec2 run-instances \
    --image-id ami-... --instance-type t3.small \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=owner,Value=NAME},{Key=Name,Value=my-box}]'
# then start/stop/terminate by instance-id; any non-owned instance -> AccessDenied
```

## Security notes

- **The `owner` tag must be set at launch** (the policy denies untagged
  launches) and **cannot be changed afterward** — `CreateTags` is allowed
  only during `RunInstances`, and `DeleteTags` is not granted.
- **No `iam:PassRole`** — instances launch with no IAM instance profile. Add
  a `PassRole` statement scoped to specific role ARNs if that's ever needed.
- **`Describe*` sees the whole account** (AWS has no resource-level condition
  on Describe); the agent just can't *touch* instances it doesn't own.
- **No spend/count cap** — IAM can't express one. Pair with AWS Budgets if
  you want a dollar guardrail.
- **Kill switch:** revoke all live sessions without rotating the key by
  attaching the `AWSRevokeOlderSessions` policy to `NAME-ec2`.

---

## `import-ssh-key`

Imports the agent's SSH **public** key into EC2 as a key pair. The agent then
launches with `--key-name` and can SSH in. The agent never needs
`ec2:ImportKeyPair` itself — you (admin) install the key once; the agent's
`RunInstances` policy already allows referencing any existing key pair.

The agent's `~/.ssh` is mode `700`, so your account can't read its `*.pub`
directly. Provide the key as a file path or with `--pubkey`. The key-pair
name defaults to the key's comment (e.g. `claude@samsung`); override with
`--key-name`.

```bash
# 1. a path your account can read (have the agent copy/cat its key out first)
./import-ssh-key /tmp/claude-id_ed25519.pub                 # named "claude@samsung"

# 2. paste the key text directly
./import-ssh-key --pubkey "ssh-ed25519 AAAA... claude@samsung"

# specific admin profile + multiple regions (key pairs are regional):
./import-ssh-key --profile=vae-admin /tmp/claude.pub --region=us-east-1,us-west-2

# pick the name explicitly; overwrite or remove:
./import-ssh-key --key-name=claude /tmp/claude.pub --replace
./import-ssh-key --key-name=claude --region=us-east-1 --delete
```

The current agent key is `ssh-ed25519 ... claude@samsung`
(`/home/claude/.ssh/id_ed25519.pub`). EC2 supports ed25519 import.

**Region must match.** `create-ec2-role` defaults the agent's CLI region
to `us-east-1`; import the key into the same region(s) or the launch fails
with `InvalidKeyPair.NotFound`.

The launch then carries both — `--key-name` must match whatever the import
named the key pair (the comment `claude@samsung` by default, or your
`--key-name` override):

```bash
aws --profile claude ec2 run-instances \
    --image-id ami-... --instance-type t3.small --key-name claude@samsung \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=owner,Value=claude},{Key=Name,Value=my-box}]'
```
