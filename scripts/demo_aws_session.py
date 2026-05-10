import os
import sys

# Ensure we can import app
sys.path.append(os.getcwd())

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from app.services.aws_session_manager import get_aws_session_manager

console = Console()

def run_demo():
    console.print(Panel.fit("[bold blue]AWS Session Manager Demo[/bold blue]\n[italic]Security: External ID Isolation & Caching[/italic]", border_style="blue"))
    
    # Configuration from environment or defaults
    role_arn = os.getenv("DEMO_ROLE_ARN")
    ext_id_1 = os.getenv("DEMO_EXT_ID_1", "demo-id-1")
    ext_id_2 = os.getenv("DEMO_EXT_ID_2", "demo-id-2")
    region = os.getenv("AWS_REGION", "us-east-1")
    
    if not role_arn:
        console.print("[bold red]ERROR:[/bold red] [yellow]DEMO_ROLE_ARN[/yellow] environment variable is required.")
        console.print("\n[bold cyan]Usage:[/bold cyan]")
        console.print("  [white]DEMO_ROLE_ARN=arn:aws:iam::... python scripts/demo_aws_session.py[/white]")
        return

    manager = get_aws_session_manager()
    
    console.print(f"\n[bold green]1.[/bold green] Requesting S3 client with ExternalId: [bold magenta]{ext_id_1}[/bold magenta]")
    try:
        client1 = manager.get_client("s3", region=region, role_arn=role_arn, external_id=ext_id_1)
        console.print(f"   [green]SUCCESS:[/green] Got client object [cyan]{id(client1)}[/cyan]")
    except Exception as e:
        console.print(f"   [bold red]FAILED:[/bold red] {e}")

    console.print(f"\n[bold green]2.[/bold green] Requesting S3 client again with SAME ExternalId: [bold magenta]{ext_id_1}[/bold magenta]")
    client1_cached = manager.get_client("s3", region=region, role_arn=role_arn, external_id=ext_id_1)
    if id(client1) == id(client1_cached):
        console.print("   [green]SUCCESS:[/green] Returned cached client (same object ID)")
    else:
        console.print("   [bold red]FAILURE:[/bold red] Returned a different object (not cached correctly)")

    console.print(f"\n[bold green]3.[/bold green] Requesting S3 client with DIFFERENT ExternalId: [bold magenta]{ext_id_2}[/bold magenta]")
    try:
        client2 = manager.get_client("s3", region=region, role_arn=role_arn, external_id=ext_id_2)
        console.print(f"   [green]SUCCESS:[/green] Got client object [cyan]{id(client2)}[/cyan]")
        if id(client1) != id(client2):
            console.print("   [green]SUCCESS:[/green] Clients are isolated (different object IDs)")
        else:
            console.print("   [bold red]FAILURE:[/bold red] Clients are the same (collision detected!)")
    except Exception as e:
        console.print(f"   [bold red]FAILED:[/bold red] {e}")

    console.print("\n[bold green]4.[/bold green] Verifying Cache Table")
    table = Table(title="AWSSessionManager Internal Cache")
    table.add_column("Service")
    table.add_column("Region")
    table.add_column("Role ARN")
    table.add_column("External ID")
    table.add_column("Object ID")

    for key, client in manager._client_cache.items():
        table.add_row(
            str(key[0]),
            str(key[1]),
            str(key[2]),
            str(key[3] or "[dim]None[/dim]"),
            f"[cyan]{id(client)}[/cyan]"
        )
    
    console.print(table)
    console.print("\n[bold blue]Demo complete.[/bold blue]")

if __name__ == "__main__":
    run_demo()
