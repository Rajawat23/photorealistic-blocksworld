# Copyright 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

from __future__ import print_function
import math, sys, random, argparse, json, os, tempfile
from datetime import datetime as dt
from collections import Counter
from copy import deepcopy as copy
  
"""
Renders random scenes using Blender, each with with a random number of objects;
each object has a random size, position, color, and shape. Objects will be
nonintersecting but may partially occlude each other. Output images will be
written to disk as PNGs, and we will also write a JSON file for each image with
ground-truth scene information.

This file expects to be run from Blender like this:

blender --background --python render_images.py -- [arguments to this script]
"""

INSIDE_BLENDER = True
try:
  import bpy, bpy_extras
  from mathutils import Vector
except ImportError as e:
  INSIDE_BLENDER = False
if INSIDE_BLENDER:
  try:
    import utils
  except ImportError as e:
    print("\nERROR")
    print("Running render_images.py from Blender and cannot import utils.py.") 
    print("You may need to add a .pth file to the site-packages of Blender's")
    print("bundled python with a command like this:\n")
    print("echo $PWD >> $BLENDER/$VERSION/python/lib/python3.5/site-packages/clevr.pth")
    print("\nWhere $BLENDER is the directory where Blender is installed, and")
    print("$VERSION is your Blender version (such as 2.78).")
    sys.exit(1)

properties         = None
color_name_to_rgba = None

def initialize_parser():
  parser = argparse.ArgumentParser()

  # Input options
  parser.add_argument('--base-scene-blendfile', default='data/base_scene.blend',
                      help="Base blender file on which all scenes are based; includes " +
                      "ground plane, lights, and camera.")
  parser.add_argument('--properties-json', default='data/properties.json',
                      help="JSON file defining objects, materials, sizes, and colors. " +
                      "The \"colors\" field maps from CLEVR color names to RGB values; " +
                      "The \"sizes\" field maps from CLEVR size names to scalars used to " +
                      "rescale object models; the \"materials\" and \"shapes\" fields map " +
                      "from CLEVR material and shape names to .blend files in the " +
                      "--object-material-dir and --shape-dir directories respectively.")
  parser.add_argument('--shape-dir', default='data/shapes',
                      help="Directory where .blend files for object models are stored")
  parser.add_argument('--material-dir', default='data/materials',
                      help="Directory where .blend files for materials are stored")
  
  # Settings for objects
  parser.add_argument('--min-objects', default=3, type=int,
                      help="The minimum number of objects to place in each scene")
  parser.add_argument('--max-objects', default=8, type=int,
                      help="The maximum number of objects to place in each scene")
  
  parser.add_argument('--min-dist', default=1.5, type=float,
                      help="the minimum distance between objects")
  parser.add_argument('--max-retries', default=50, type=int,
                      help="The number of times to try placing an object before giving up and " +
                      "re-placing all objects in the scene.")
  parser.add_argument('--min-pixels-per-object', default=200, type=int,
                      help="All objects will have at least this many visible pixels in the " +
                      "final rendered images; this ensures that no objects are fully " +
                      "occluded by other objects.")
  
  # Output settings
  parser.add_argument('--start-idx', default=0, type=int,
                      help="The index at which to start for numbering rendered images. Setting " +
                      "this to non-zero values allows you to distribute rendering across " +
                      "multiple machines and recombine the results later.")
  parser.add_argument('--num-images', default=float('inf'), type=float,
                      help="The number of images to render")
  
  parser.add_argument('--filename-prefix', default='CLEVR',
                      help="This prefix will be prepended to the rendered images and JSON scenes")
  parser.add_argument('--split', default='new',
                      help="Name of the split for which we are rendering. This will be added to " +
                      "the names of rendered images, and will also be stored in the JSON " +
                      "scene structure for each image.")
  parser.add_argument('--output-dir', default='output',
                      help="The directory where output will be stored. It will be " +
                      "created if it does not exist.")
  parser.add_argument('--save-blendfiles', type=int, default=0,
                      help="Setting --save-blendfiles 1 will cause the blender scene file for " +
                      "each generated image to be stored in the directory specified by " +
                      "the --output-blend-dir flag. These files are not saved by default " +
                      "because they take up ~5-10MB each.")
  parser.add_argument('--version', default='1.0',
                      help="String to store in the \"version\" field of the generated JSON file")
  parser.add_argument('--license',
                      default="Creative Commons Attribution (CC-BY 4.0)",
                      help="String to store in the \"license\" field of the generated JSON file")
  parser.add_argument('--date', default=dt.today().strftime("%m/%d/%Y"),
                      help="String to store in the \"date\" field of the generated JSON file; " +
                      "defaults to today's date")
  
  # Rendering options
  parser.add_argument('--use-gpu', default=0, type=int,
                      help="Setting --use-gpu 1 enables GPU-accelerated rendering using CUDA. " +
                      "You must have an NVIDIA GPU with the CUDA toolkit installed for " +
                      "to work.")
  parser.add_argument('--width', default=320, type=int,
                      help="The width (in pixels) for the rendered images")
  parser.add_argument('--height', default=240, type=int,
                      help="The height (in pixels) for the rendered images")
  parser.add_argument('--key-light-jitter', default=1.0, type=float,
                      help="The magnitude of random jitter to add to the key light position.")
  parser.add_argument('--fill-light-jitter', default=1.0, type=float,
                      help="The magnitude of random jitter to add to the fill light position.")
  parser.add_argument('--back-light-jitter', default=1.0, type=float,
                      help="The magnitude of random jitter to add to the back light position.")
  parser.add_argument('--camera-jitter', default=0.5, type=float,
                      help="The magnitude of random jitter to add to the camera position")
  parser.add_argument('--render-num-samples', default=512, type=int,
                      help="The number of samples to use when rendering. Larger values will " +
                      "result in nicer images but will cause rendering to take longer.")
  parser.add_argument('--render-min-bounces', default=8, type=int,
                      help="The minimum number of bounces to use for rendering.")
  parser.add_argument('--render-max-bounces', default=8, type=int,
                      help="The maximum number of bounces to use for rendering.")
  parser.add_argument('--render-tile-size', default=256, type=int,
                      help="The tile size to use for rendering. This should not affect the " +
                      "quality of the rendered image but may affect the speed; CPU-based " +
                      "rendering may achieve better performance using smaller tile sizes " +
                      "while larger tile sizes may be optimal for GPU-based rendering.")
  parser.add_argument('--dry-run', default=False, action='store_true',
                      help="Do not render images and count the number of possible states and transitions.")
  return parser

  
def main(args):
  # Load the property file
  global properties, color_name_to_rgba
  with open(args.properties_json, 'r') as f:
    properties = json.load(f)
    color_name_to_rgba = {
      name : [float(c) / 255.0 for c in rgb] + [1.0] \
      for name, rgb in properties['colors'].items()
    }
  
  num_digits = 6
  prefix = '%s_%s_' % (args.filename_prefix, args.split)
  template = '%s%%0%dd' % (prefix, num_digits)

  img_dir         = os.path.join(args.output_dir,"image")
  scene_dir       = os.path.join(args.output_dir,"scene")
  blend_dir       = os.path.join(args.output_dir,"blend")
  trans_img_dir   = os.path.join(args.output_dir,"image_tr")
  trans_scene_dir = os.path.join(args.output_dir,"scene_tr")
  trans_blend_dir = os.path.join(args.output_dir,"blend_tr")

  for d in [img_dir,
            scene_dir,
            blend_dir,
            trans_img_dir,
            trans_scene_dir,
            trans_blend_dir]:
    if not os.path.isdir(d):
      os.makedirs(d)

  img_template         = os.path.join(img_dir,  template)
  scene_template       = os.path.join(scene_dir,template)
  blend_template       = os.path.join(blend_dir,template)
  trans_img_template   = os.path.join(trans_img_dir,  template)
  trans_scene_template = os.path.join(trans_scene_dir,template)
  trans_blend_template = os.path.join(trans_blend_dir,template)

  i = args.start_idx
  while i < args.start_idx + args.num_images:
    if args.dry_run:
      continue
      
    objects = initialize_objects(args)

    img_path = img_template % i +".png"
    scene_path = scene_template % i+".json"
    blend_path = blend_template % i
    
    result_ok = render_scene(args,
                 output_index=i,
                 output_split=args.split,
                 output_image=img_path,
                 output_scene=scene_path,
                 # output_blendfile=blend_path,
                 objects=objects)
    
    if result_ok:
        i += 1


    
def initialize_objects(args):
    num_objects = random.randint(args.min_objects, args.max_objects)
    
    def dist(o1, o2):
      x1,y1,_ = o1["location"]
      x2,y2,_ = o2["location"]
      dx=x1-x2
      dy=y1-y2
      return math.sqrt(dx * dx + dy * dy)

    def dist_good(o1, o2):
        return dist(o1,o2) - o1["size"] - o2["size"] > args.min_dist
            
    objects         = []
    i = 0
    while i < num_objects:
        num_tries = 0
        success = False
        while num_tries < args.max_retries:
            shape_name, shape_path = random_dict(properties['shapes'])
            _, rgba                = random_dict(color_name_to_rgba)
            _, r                   = random_dict(properties['sizes'])
            _, m                   = random_dict(properties['materials'])
            rotation               = 360.0 * random.random()
            # For cube, adjust the size a bit
            if shape_name == 'cube':
                r /= math.sqrt(2)

            o1 = {
                'shape': shape_path,
                'size': r,
                'stackable': properties['stackable'][shape_name] == 1,
                'rotation': rotation,
                'color':tuple(rgba),
                'material': m,
                'location': (random.uniform(-3, 3),
                             random.uniform(-3, 3),
                             r)
            }

            good = True
            for o2 in objects:
                if not dist_good(o1,o2):
                    good = False
                    break

            if good:
                objects.append(o1)
                success = True
                break

            num_tries += 1
            
        if not success:
            objects = []
            i       = 0
            continue
        i += 1
    
    return objects

def render_scene(args,
    output_index=0,
    output_split='none',
    output_image='render.png',
    output_scene='render_json',
    output_blendfile=None,
    objects=[],
  ):

  # Load the main blendfile
  bpy.ops.wm.open_mainfile(filepath=args.base_scene_blendfile)

  # Load materials
  utils.load_materials(args.material_dir)

  # Set render arguments so we can get pixel coordinates later.
  # We use functionality specific to the CYCLES renderer so BLENDER_RENDER
  # cannot be used.
  render_args = bpy.context.scene.render
  render_args.engine = "CYCLES"
  render_args.filepath = output_image
  render_args.resolution_x = args.width
  render_args.resolution_y = args.height
  render_args.resolution_percentage = 100
  render_args.tile_x = args.render_tile_size
  render_args.tile_y = args.render_tile_size
  if args.use_gpu == 1:
    # Blender changed the API for enabling CUDA at some point
    if bpy.app.version < (2, 78, 0):
      bpy.context.user_preferences.system.compute_device_type = 'CUDA'
      bpy.context.user_preferences.system.compute_device = 'CUDA_0'
    else:
      cycles_prefs = bpy.context.user_preferences.addons['cycles'].preferences
      cycles_prefs.compute_device_type = 'CUDA'

  # Some CYCLES-specific stuff
  bpy.data.worlds['World'].cycles.sample_as_light = True
  bpy.context.scene.cycles.blur_glossy = 2.0
  bpy.context.scene.cycles.samples = args.render_num_samples
  bpy.context.scene.cycles.transparent_min_bounces = args.render_min_bounces
  bpy.context.scene.cycles.transparent_max_bounces = args.render_max_bounces
  if args.use_gpu == 1:
    bpy.context.scene.cycles.device = 'GPU'

  # This will give ground-truth information about the scene and its objects
  scene_struct = {
      'split': output_split,
      'image_index': output_index,
      'image_filename': os.path.basename(output_image),
      'objects': [],
      'directions': {},
  }

  # Put a plane on the ground so we can compute cardinal directions
  bpy.ops.mesh.primitive_plane_add(radius=5)
  plane = bpy.context.object

  def rand(L):
    return 2.0 * L * (random.random() - 0.5)

  # Add random jitter to camera position
  if args.camera_jitter > 0:
    for i in range(3):
      bpy.data.objects['Camera'].location[i] += rand(args.camera_jitter)

  # Figure out the left, up, and behind directions along the plane and record
  # them in the scene structure
  camera = bpy.data.objects['Camera']
  plane_normal = plane.data.vertices[0].normal
  cam_behind = camera.matrix_world.to_quaternion() * Vector((0, 0, -1))
  cam_left = camera.matrix_world.to_quaternion() * Vector((-1, 0, 0))
  cam_up = camera.matrix_world.to_quaternion() * Vector((0, 1, 0))
  plane_behind = (cam_behind - cam_behind.project(plane_normal)).normalized()
  plane_left = (cam_left - cam_left.project(plane_normal)).normalized()
  plane_up = cam_up.project(plane_normal).normalized()

  # Delete the plane; we only used it for normals anyway. The base scene file
  # contains the actual ground plane.
  utils.delete_object(plane)

  # Save all six axis-aligned directions in the scene struct
  scene_struct['directions']['behind'] = tuple(plane_behind)
  scene_struct['directions']['front'] = tuple(-plane_behind)
  scene_struct['directions']['left'] = tuple(plane_left)
  scene_struct['directions']['right'] = tuple(-plane_left)
  scene_struct['directions']['above'] = tuple(plane_up)
  scene_struct['directions']['below'] = tuple(-plane_up)

  # Add random jitter to lamp positions
  if args.key_light_jitter > 0:
    for i in range(3):
      bpy.data.objects['Lamp_Key'].location[i] += rand(args.key_light_jitter)
  if args.back_light_jitter > 0:
    for i in range(3):
      bpy.data.objects['Lamp_Back'].location[i] += rand(args.back_light_jitter)
  if args.fill_light_jitter > 0:
    for i in range(3):
      bpy.data.objects['Lamp_Fill'].location[i] += rand(args.fill_light_jitter)

  # Now make some random objects
  blender_objects = add_objects(scene_struct, camera, objects)

  def cleanup():
    for obj in blender_objects:
      utils.delete_object(obj)
        
  for obj in objects:
      x1,y1,x2,y2 = obj["bbox"]
      if x1<0 or args.width < x1:
        cleanup()
        return False
      if x2<0 or args.width < x2:
        cleanup()
        return False
      if y1<0 or args.height < y1:
        cleanup()
        return False
      if y2<0 or args.height < y2:
        cleanup()
        return False
  
  all_visible = check_visibility(blender_objects, args.min_pixels_per_object)
  if not all_visible:
    print('Some objects are occluded; replacing objects')
    cleanup()
    return False

  # Render the scene and dump the scene data structure
  scene_struct['objects'] = objects
  scene_struct['relationships'] = compute_all_relationships(scene_struct)
  while True:
    try:
      bpy.ops.render.render(write_still=True)
      break
    except Exception as e:
      print(e)

  with open(output_scene, 'w') as f:
    json.dump(scene_struct, f, indent=2)

  if output_blendfile is not None:
    bpy.ops.wm.save_as_mainfile(filepath=output_blendfile)
  
  return True

def random_dict(dict):
  return random.choice(list(dict.items()))

def object_equal(o1, o2):
  return \
    o1["shape"]    == o2["shape"]    and \
    o1["size"]     == o2["size"]     and \
    o1["color"]    == o2["color"]

def add_objects(scene_struct, camera, objects):
  """
  Add objects to the current blender scene
  """
  blender_objects = []
  for obj in objects:
    
    # Actually add the object to the scene
    utils.add_object(args.shape_dir,
                     obj["shape"],
                     obj["size"],
                     obj["location"],
                     theta=obj["rotation"])
    bobj = bpy.context.object
    blender_objects.append(bobj)
    utils.add_material(obj["material"], Color=obj["color"])
    obj["pixel_coords"] = utils.get_camera_coords(camera, bobj.location)

    import numpy as np
    loc = np.array(bobj.location)
    dim = np.array(bobj.dimensions)
    half = dim / 2
    corners = []
    corners.append(loc + half * [1,1,1])
    corners.append(loc + half * [1,1,-1])
    corners.append(loc + half * [1,-1,1])
    corners.append(loc + half * [1,-1,-1])
    corners.append(loc + half * [-1,1,1])
    corners.append(loc + half * [-1,1,-1])
    corners.append(loc + half * [-1,-1,1])
    corners.append(loc + half * [-1,-1,-1])

    import mathutils
    corners_camera_coords = np.array([ utils.get_camera_coords(camera, mathutils.Vector(tuple(corner)))
                                       for corner in corners ])
    xmax = np.amax(corners_camera_coords[:,0])
    ymax = np.amax(corners_camera_coords[:,1])
    xmin = np.amin(corners_camera_coords[:,0])
    ymin = np.amin(corners_camera_coords[:,1])
    obj["bbox"] = (float(xmin), float(ymin), float(xmax), float(ymax))
  return blender_objects

def compute_all_relationships(scene_struct, eps=0.2):
  """
  Computes relationships between all pairs of objects in the scene.
  
  Returns a dictionary mapping string relationship names to lists of lists of
  integers, where output[rel][i] gives a list of object indices that have the
  relationship rel with object i. For example if j is in output['left'][i] then
  object j is left of object i.
  """
  all_relationships = {}
  for name, direction_vec in scene_struct['directions'].items():
    if name == 'above' or name == 'below': continue
    all_relationships[name] = []
    for i, obj1 in enumerate(scene_struct['objects']):
      coords1 = obj1['location']
      related = set()
      for j, obj2 in enumerate(scene_struct['objects']):
        if obj1 == obj2: continue
        coords2 = obj2['location']
        diff = [coords2[k] - coords1[k] for k in [0, 1, 2]]
        dot = sum(diff[k] * direction_vec[k] for k in [0, 1, 2])
        if dot > eps:
          related.add(j)
      all_relationships[name].append(sorted(list(related)))
  return all_relationships


def check_visibility(blender_objects, min_pixels_per_object):
  """
  Check whether all objects in the scene have some minimum number of visible
  pixels; to accomplish this we assign random (but distinct) colors to all
  objects, and render using no lighting or shading or antialiasing; this
  ensures that each object is just a solid uniform color. We can then count
  the number of pixels of each color in the output image to check the visibility
  of each object.

  Returns True if all objects are visible and False otherwise.
  """
  f, path = tempfile.mkstemp(suffix='.png')
  object_colors = render_shadeless(blender_objects, path=path)
  img = bpy.data.images.load(path)
  p = list(img.pixels)
  color_count = Counter((p[i], p[i+1], p[i+2], p[i+3])
                        for i in range(0, len(p), 4))
  os.remove(path)
  if len(color_count) != len(blender_objects) + 1:
    return False
  for _, count in color_count.most_common():
    if count < min_pixels_per_object:
      return False
  return True


def render_shadeless(blender_objects, path='flat.png'):
  """
  Render a version of the scene with shading disabled and unique materials
  assigned to all objects, and return a set of all colors that should be in the
  rendered image. The image itself is written to path. This is used to ensure
  that all objects will be visible in the final rendered scene.
  """
  render_args = bpy.context.scene.render

  # Cache the render args we are about to clobber
  old_filepath = render_args.filepath
  old_engine = render_args.engine
  old_use_antialiasing = render_args.use_antialiasing

  # Override some render settings to have flat shading
  render_args.filepath = path
  render_args.engine = 'BLENDER_RENDER'
  render_args.use_antialiasing = False

  # Move the lights and ground to layer 2 so they don't render
  utils.set_layer(bpy.data.objects['Lamp_Key'], 2)
  utils.set_layer(bpy.data.objects['Lamp_Fill'], 2)
  utils.set_layer(bpy.data.objects['Lamp_Back'], 2)
  utils.set_layer(bpy.data.objects['Ground'], 2)

  # Add random shadeless materials to all objects
  object_colors = set()
  old_materials = []
  for i, obj in enumerate(blender_objects):
    old_materials.append(obj.data.materials[0])
    bpy.ops.material.new()
    mat = bpy.data.materials['Material']
    mat.name = 'Material_%d' % i
    while True:
      r, g, b = [random.random() for _ in range(3)]
      if (r, g, b) not in object_colors: break
    object_colors.add((r, g, b))
    mat.diffuse_color = [r, g, b]
    mat.use_shadeless = True
    obj.data.materials[0] = mat

  # Render the scene
  bpy.ops.render.render(write_still=True)

  # Undo the above; first restore the materials to objects
  for mat, obj in zip(old_materials, blender_objects):
    obj.data.materials[0] = mat

  # Move the lights and ground back to layer 0
  utils.set_layer(bpy.data.objects['Lamp_Key'], 0)
  utils.set_layer(bpy.data.objects['Lamp_Fill'], 0)
  utils.set_layer(bpy.data.objects['Lamp_Back'], 0)
  utils.set_layer(bpy.data.objects['Ground'], 0)

  # Set the render settings back to what they were
  render_args.filepath = old_filepath
  render_args.engine = old_engine
  render_args.use_antialiasing = old_use_antialiasing

  return object_colors


if __name__ == '__main__':
  parser = initialize_parser()
  if INSIDE_BLENDER:
    # Run normally
    argv = utils.extract_args()
    args = parser.parse_args(argv)
    main(args)
  elif '--help' in sys.argv or '-h' in sys.argv:
    parser.print_help()
  else:
    print('This script is intended to be called from blender like this:')
    print()
    print('blender --background --python render_images.py -- [args]')
    print()
    print('You can also run as a standalone python script to view all')
    print('arguments like this:')
    print()
    print('python render_images.py --help')

