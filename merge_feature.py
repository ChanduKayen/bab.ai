import subprocess
import sys

def run_cmd(cmd: str):
    """Run a shell command and stream output"""
    print(f"\n>>> {cmd}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"Command failed: {cmd}")
        sys.exit(result.returncode)

def main():
    branch = "feature/whstapp-flow"

    # 1. Checkout feature branch
    run_cmd(f"git checkout {branch}")

    # 2. Stage all changes
    run_cmd("git add .")

    # 3. Commit with user message
    if len(sys.argv) < 2:
        print("Usage: python merge_feature.py '<commit message>'")
        sys.exit(1)
    commit_msg = sys.argv[1]
    run_cmd(f'git commit -m "{commit_msg}"')

    # 4. Push feature branch
    run_cmd(f"git push origin {branch}")

    # 5. Checkout main
    run_cmd("git checkout main")

    # 6. Pull latest main
    run_cmd("git pull origin main")

    # 7. Merge feature branch into main
    run_cmd(f"git merge {branch}")

    # 8. Push main
    run_cmd("git push origin main")

if __name__ == "__main__":
    main()
