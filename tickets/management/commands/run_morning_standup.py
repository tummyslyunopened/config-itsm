from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'No-op. Morning standup has been removed.'

    def handle(self, *args, **options):
        self.stdout.write('Morning standup has been removed. This command does nothing.')
