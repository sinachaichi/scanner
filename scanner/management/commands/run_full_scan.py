from django.core.management.base import BaseCommand

from scanner.actions import run_full_scan


class Command(BaseCommand):
    help = 'Run a full scan of all sources and persist working nodes.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('Starting full scan...'))
        run_full_scan()
        self.stdout.write(self.style.SUCCESS('Full scan completed.'))
