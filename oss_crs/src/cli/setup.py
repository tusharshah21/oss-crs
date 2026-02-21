"""Setup command for oss-crs cgroup-parent mode.

This module implements the `oss-crs setup` command which configures
the host system for cgroup-parent based resource management.
"""

import subprocess
from rich.console import Console
from rich.panel import Panel
import questionary

from ..cgroup import (
    check_docker_cgroup_driver,
    check_cgroup_delegation,
    check_oss_crs_directory,
    check_oss_crs_controllers,
    enable_oss_crs_controllers,
    generate_docker_config_commands,
    generate_cgroup_setup_commands,
    get_user_cgroup_base,
    get_user_service_cgroup,
)


console = Console()


def print_status(label: str, ok: bool, detail: str = "") -> None:
    """Print a status line with checkmark or cross."""
    icon = "[green]OK[/green]" if ok else "[red]X[/red]"
    detail_text = f" - {detail}" if detail else ""
    console.print(f"  [{icon}] {label}{detail_text}")


def print_step(step_num: int, description: str) -> None:
    """Print a numbered step header."""
    console.print(f"\n[bold cyan]Step {step_num}:[/bold cyan] {description}")


def confirm_command(description: str, command: str) -> bool:
    """Ask user to confirm running a privileged command.

    Args:
        description: Human-readable description of what the command does
        command: The actual command to run

    Returns:
        True if user confirmed and command succeeded, False otherwise
    """
    console.print(f"\n[yellow]Action:[/yellow] {description}")
    console.print(f"[dim]Command:[/dim] {command}")

    answer = questionary.confirm(
        "Run this command?",
        default=True,
    ).ask()

    if answer is None:  # User pressed Ctrl+C
        console.print("[yellow]Aborted by user[/yellow]")
        return False

    if not answer:
        console.print("[yellow]Skipped[/yellow]")
        return False

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            console.print("[green]Success[/green]")
            return True
        else:
            console.print(f"[red]Failed:[/red] {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        console.print("[red]Command timed out[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        return False


def run_setup(check_only: bool = False) -> bool:
    """Run the setup process for cgroup-parent mode.

    Args:
        check_only: If True, only check status without making changes

    Returns:
        True if setup is complete (or check passes), False otherwise
    """
    console.print(Panel(
        "[bold]oss-crs setup[/bold]\n\n"
        "This command configures your system for cgroup-parent mode,\n"
        "which enables fine-grained CPU and memory resource control\n"
        "for CRS containers without Docker-in-Docker.",
        title="Cgroup-Parent Setup",
        border_style="blue",
    ))

    all_ok = True
    needs_docker_config = False
    needs_cgroup_setup = False
    needs_controller_enable = False

    # === Check 1: Docker cgroup driver ===
    console.print("\n[bold]Checking requirements...[/bold]")

    is_cgroupfs, driver = check_docker_cgroup_driver()
    if driver == "docker_not_found":
        print_status("Docker installed", False, "Docker not found in PATH")
        console.print("\n[red]Docker must be installed to continue.[/red]")
        return False
    elif driver == "timeout":
        print_status("Docker daemon", False, "Docker daemon not responding")
        console.print("\n[red]Docker daemon must be running to continue.[/red]")
        return False
    elif is_cgroupfs:
        print_status("Docker cgroup driver", True, "cgroupfs")
    else:
        print_status("Docker cgroup driver", False, f"using '{driver}', needs 'cgroupfs'")
        all_ok = False
        needs_docker_config = True

    # === Check 2: Cgroup v2 delegation ===
    delegation_ok, missing_controllers = check_cgroup_delegation()
    if delegation_ok:
        print_status("Cgroup v2 delegation", True, "cpuset, memory enabled")
    else:
        detail = f"missing: {', '.join(missing_controllers)}" if missing_controllers else "not configured"
        print_status("Cgroup v2 delegation", False, detail)
        all_ok = False
        needs_cgroup_setup = True

    # === Check 3: oss-crs directory ===
    dir_ok, dir_status = check_oss_crs_directory()
    if dir_ok:
        print_status("oss-crs cgroup directory", True, str(get_user_cgroup_base()))
    else:
        detail = {
            "not_exists": "directory does not exist",
            "permission_denied": "permission denied",
        }.get(dir_status, dir_status)
        print_status("oss-crs cgroup directory", False, detail)
        all_ok = False
        needs_cgroup_setup = True

    # === Check 4: oss-crs controllers (only if directory exists) ===
    if dir_ok:
        controllers_ok, missing = check_oss_crs_controllers()
        if controllers_ok:
            print_status("oss-crs controllers", True, "cpuset, memory enabled")
        else:
            detail = f"missing: {', '.join(missing)}" if missing else "not enabled"
            print_status("oss-crs controllers", False, detail)
            all_ok = False
            needs_controller_enable = True

    if all_ok:
        console.print("\n[bold green]All checks passed![/bold green] Your system is ready for cgroup-parent mode.")
        return True

    if check_only:
        console.print("\n[yellow]Some checks failed.[/yellow] Run [bold]oss-crs setup[/bold] to fix issues.")
        return False

    # === Interactive Setup ===
    console.print("\n[bold]Setup required[/bold]")
    console.print("Some configuration is needed. The following changes require [bold]sudo[/bold] privileges.")
    console.print("You will be asked to confirm each command before it runs.\n")

    step_num = 1

    # === Step: Configure Docker ===
    if needs_docker_config:
        print_step(step_num, "Configure Docker to use cgroupfs driver")
        step_num += 1

        console.print("""
Docker must use the [bold]cgroupfs[/bold] cgroup driver instead of [bold]systemd[/bold].
This change requires modifying /etc/docker/daemon.json and restarting Docker.

[yellow]Warning:[/yellow] This will restart Docker daemon. Running containers will be stopped.
""")

        proceed = questionary.confirm(
            "Proceed with Docker configuration?",
            default=True,
        ).ask()

        if proceed is None:
            console.print("[yellow]Aborted by user[/yellow]")
            return False

        if proceed:
            commands = generate_docker_config_commands()
            for desc, cmd in commands:
                if not confirm_command(desc, cmd):
                    console.print("[yellow]Docker configuration incomplete. Please complete manually.[/yellow]")
                    return False

            # Verify Docker config
            is_cgroupfs, driver = check_docker_cgroup_driver()
            if is_cgroupfs:
                console.print("[green]Docker cgroup driver configured successfully![/green]")
            else:
                console.print(f"[red]Docker still using '{driver}' driver. Please check configuration.[/red]")
                return False
        else:
            console.print("[yellow]Skipping Docker configuration.[/yellow]")
            console.print("You can configure Docker manually by adding to /etc/docker/daemon.json:")
            console.print('[dim]{"exec-opts": ["native.cgroupdriver=cgroupfs"]}[/dim]')

    # === Step: Set up cgroup delegation ===
    if needs_cgroup_setup:
        print_step(step_num, "Set up cgroup v2 delegation")
        step_num += 1

        user_service = get_user_service_cgroup()
        oss_crs_path = get_user_cgroup_base()

        console.print(f"""
Cgroup v2 requires proper delegation to allow non-root users to manage resources.
This will:
1. Enable cpuset and memory controllers at [dim]{user_service}[/dim]
2. Create the oss-crs directory at [dim]{oss_crs_path}[/dim]
3. Set ownership so you can manage cgroups without sudo
""")

        proceed = questionary.confirm(
            "Proceed with cgroup setup?",
            default=True,
        ).ask()

        if proceed is None:
            console.print("[yellow]Aborted by user[/yellow]")
            return False

        if proceed:
            commands = generate_cgroup_setup_commands()
            for desc, cmd in commands:
                if not confirm_command(desc, cmd):
                    console.print("[yellow]Cgroup setup incomplete. Please complete manually.[/yellow]")
                    return False
            # Controllers are now enabled as part of cgroup setup commands
        else:
            console.print("[yellow]Skipping cgroup setup.[/yellow]")

    # === Step: Enable controllers at oss-crs level (only if dir exists but controllers not enabled) ===
    elif needs_controller_enable:
        print_step(step_num, "Enable controllers in oss-crs cgroup")
        step_num += 1

        console.print("Enabling cpuset and memory controllers at oss-crs level...")
        success, message = enable_oss_crs_controllers()
        if success:
            console.print(f"[green]{message}[/green]")
        else:
            # Try with sudo as fallback
            console.print(f"[yellow]Direct write failed: {message}[/yellow]")
            console.print("Attempting with sudo...")
            oss_crs_path = get_user_cgroup_base()
            if not confirm_command(
                "Enable controllers at oss-crs level",
                f'echo "+cpuset +memory" | sudo tee {oss_crs_path}/cgroup.subtree_control'
            ):
                console.print("[red]Failed to enable controllers.[/red]")
                return False

    # === Final verification ===
    console.print("\n[bold]Verifying setup...[/bold]")

    is_cgroupfs, driver = check_docker_cgroup_driver()
    print_status("Docker cgroup driver", is_cgroupfs, driver)

    delegation_ok, _ = check_cgroup_delegation()
    print_status("Cgroup v2 delegation", delegation_ok)

    dir_ok, _ = check_oss_crs_directory()
    print_status("oss-crs cgroup directory", dir_ok)

    if dir_ok:
        controllers_ok, _ = check_oss_crs_controllers()
        print_status("oss-crs controllers", controllers_ok)
    else:
        controllers_ok = False

    all_ok = is_cgroupfs and delegation_ok and dir_ok and controllers_ok

    if all_ok:
        console.print("\n[bold green]Setup complete![/bold green] Your system is ready for cgroup-parent mode.")
        console.print("\nYou can now use [bold]--cgroup-parent[/bold] with oss-crs commands:")
        console.print("  [dim]oss-crs build-target --cgroup-parent ...[/dim]")
        console.print("  [dim]oss-crs run --cgroup-parent ...[/dim]")
    else:
        console.print("\n[yellow]Setup incomplete.[/yellow] Some checks are still failing.")
        console.print("Please address the issues above and run [bold]oss-crs setup[/bold] again.")

    return all_ok


def add_setup_command(subparsers) -> None:
    """Add the setup command to the CLI parser."""
    setup = subparsers.add_parser(
        "setup",
        help="Configure system for cgroup-parent resource management"
    )
    setup.add_argument(
        "--check",
        action="store_true",
        help="Only check status without making changes",
    )


def handle_setup(args) -> bool:
    """Handle the setup command.

    Args:
        args: Parsed command line arguments

    Returns:
        True if setup succeeded, False otherwise
    """
    return run_setup(check_only=args.check)
