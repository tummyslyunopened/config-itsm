from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run the 4am morning agent standup for all eligible engineers.'

    def handle(self, *args, **options):
        from agents.runner import run_morning
        self.stdout.write('Starting morning standup…')
        run_morning()
        self.stdout.write(self.style.SUCCESS('Morning standup complete.'))
