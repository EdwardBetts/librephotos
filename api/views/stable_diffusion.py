import os
import uuid
from datetime import datetime

import PIL
import pytz
from diffusers import StableDiffusionImg2ImgPipeline, StableDiffusionPipeline
from django.http import HttpResponse
from django_rq import job
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView
from torch import autocast

import ownphotos.settings
from api.models import LongRunningJob, Photo
from api.util import logger


@job
def generate_image(user, job_id, prompt):
    if LongRunningJob.objects.filter(job_id=job_id).exists():
        lrj = LongRunningJob.objects.get(job_id=job_id)
        lrj.started_at = datetime.now().replace(tzinfo=pytz.utc)
        lrj.save()
    else:
        lrj = LongRunningJob.objects.create(
            started_by=user,
            job_id=job_id,
            queued_at=datetime.now().replace(tzinfo=pytz.utc),
            started_at=datetime.now().replace(tzinfo=pytz.utc),
            job_type=LongRunningJob.JOB_GENERATE_PHOTO,
        )
        lrj.save()
    try:

        pipe = StableDiffusionPipeline.from_pretrained("/stable-diffusion")

        # improve speed by deactivating check
        def dummy_checker(images, **kwargs):
            return images, False

        pipe.safety_checker = dummy_checker

        pipe = pipe.to("cpu")

        with autocast("cpu"):
            image = pipe(prompt)["sample"][0]

        # save image in folder /generated and renamed it if it already exists
        if not os.path.exists(os.path.join(user.scan_directory, "generated")):
            os.mkdir(os.path.join(user.scan_directory, "generated"))
        if not os.path.exists(
            os.path.join(user.scan_directory, "generated", prompt + ".jpg")
        ):
            photo_path = os.path.join(user.scan_directory, "generated", prompt + ".jpg")
        else:
            image_hash = uuid.uuid4().hex
            photo_path = os.path.join(
                user.scan_directory, "generated", image_hash + ".jpg"
            )

        image_path = os.path.join(ownphotos.settings.BASE_LOGS, prompt + ".jpg")
        # To-Do: save image with without permissions
        image.save(photo_path)

        logger.info("Picture is in {}".format(image_path))
        lrj.finished = True
        lrj.finished_at = datetime.now().replace(tzinfo=pytz.utc)
        lrj.save()
        logger.info("job {}: updated lrj entry to db".format(job_id))

    except Exception:
        logger.exception("An error occured")
        lrj.failed = True
        lrj.finished = True
        lrj.finished_at = datetime.now().replace(tzinfo=pytz.utc)
        lrj.save()

    return 1


@job
def generate_altered_image(user, job_id, prompt, image_hash):
    if LongRunningJob.objects.filter(job_id=job_id).exists():
        lrj = LongRunningJob.objects.get(job_id=job_id)
        lrj.started_at = datetime.now().replace(tzinfo=pytz.utc)
        lrj.save()
    else:
        lrj = LongRunningJob.objects.create(
            started_by=user,
            job_id=job_id,
            queued_at=datetime.now().replace(tzinfo=pytz.utc),
            started_at=datetime.now().replace(tzinfo=pytz.utc),
            job_type=LongRunningJob.JOB_GENERATE_PHOTO,
        )
        lrj.save()
    try:

        pipe = StableDiffusionImg2ImgPipeline.from_pretrained("/stable-diffusion")

        try:
            photo = Photo.objects.get(image_hash=image_hash)
        except Photo.DoesNotExist:
            return HttpResponse(status=404)

        image = PIL.Image.open(photo.thumbnail_big.path).convert("RGB")
        # To-Do: Check the RAM requriements for the different sizes like SD, HD, FullHD, 4K
        image = image.resize((768, 512))
        # improve speed by deactivating check
        def dummy_checker(images, **kwargs):
            return images, False

        pipe.safety_checker = dummy_checker

        pipe = pipe.to("cpu")

        with autocast("cpu"):
            result = pipe(prompt=prompt, init_image=image).images[0]

        # save image in folder /generated and renamed it if it already exists
        if not os.path.exists(os.path.join(user.scan_directory, "generated")):
            os.mkdir(os.path.join(user.scan_directory, "generated"))
        if not os.path.exists(
            os.path.join(user.scan_directory, "generated", prompt + ".jpg")
        ):
            photo_path = os.path.join(user.scan_directory, "generated", prompt + ".jpg")
        else:
            image_hash = uuid.uuid4().hex
            photo_path = os.path.join(
                user.scan_directory, "generated", image_hash + ".jpg"
            )

        image_path = os.path.join(ownphotos.settings.BASE_LOGS, prompt + ".jpg")
        result.save(photo_path)

        logger.info("Picture is in {}".format(image_path))
        lrj.finished = True
        lrj.finished_at = datetime.now().replace(tzinfo=pytz.utc)
        lrj.save()
        logger.info("job {}: updated lrj entry to db".format(job_id))

    except Exception:
        logger.exception("An error occured")
        lrj.failed = True
        lrj.finished = True
        lrj.finished_at = datetime.now().replace(tzinfo=pytz.utc)
        lrj.save()

    return 1


# This API View calls generate image and returns the job id and has a query prompt parameter
class StableDiffusionView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("prompt", OpenApiTypes.STR),
        ],
    )
    def get(self, request, format=None):
        job_id = uuid.uuid4()
        prompt = request.query_params.get("prompt")
        generate_image.delay(request.user, job_id, prompt)
        return Response({"job_id": job_id})


# This API View calls generate image and returns the job id and has a query prompt parameter and a image-hash parameter
class StableDiffusionAlteredView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("prompt", OpenApiTypes.STR),
            OpenApiParameter("image_hash", OpenApiTypes.STR),
        ],
    )
    def get(self, request, format=None):
        job_id = uuid.uuid4()
        prompt = request.query_params.get("prompt")
        image_hash = request.query_params.get("image_hash")
        generate_altered_image.delay(request.user, job_id, prompt, image_hash)
        return Response({"job_id": job_id})
