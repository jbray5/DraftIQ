from rest_framework.decorators import api_view
from rest_framework.response import Response
from .logic import suggest_next_pick

@api_view(['GET'])
def suggest_next_pick_view(request):
    result = suggest_next_pick()
    return Response(result)
