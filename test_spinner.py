import time

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner

console = Console()

print("Testing spinner for 3 seconds...")
spinner = Spinner("dots12", text="thinking...", style="bold orange1")
with Live(spinner, console=console, refresh_per_second=20, transient=True):
    time.sleep(3)
print("Done! Spinner worked.")
