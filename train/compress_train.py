#!/usr/bin/env python

import os.path
import time

import numpy as np
import tensorflow as tf

import sys
sys.path.append('../')
from model.lat_net import *
from model.loss import *
from model.optimizer import *
from utils.experiment_manager import make_checkpoint_path

FLAGS = tf.app.flags.FLAGS

TRAIN_DIR = make_checkpoint_path(FLAGS.base_dir, FLAGS)

def train():
  """Train lat net for a number of steps."""

  with tf.Graph().as_default():
    # print important params
    print("Training on " + FLAGS.system + " system...")
    print("Dimensions are " + FLAGS.dimensions + "x" + str(FLAGS.lattice_size))

    # store grad and loss values
    grads = []
    loss_gen = []

    # global step counter
    global_step = tf.get_variable('global_step', [], initializer=tf.constant_initializer(0), trainable=False)

    # do for all gpus
    print("Unrolling network on all gpus...")
    for i in range(FLAGS.nr_gpus):
      print("Unrolling on gpu:" + str(i))

      # make input que runner for gpu
      state, boundary = inputs() 

      # hard set gpu
      with tf.device('/gpu:%d' % i):
        # unroll on gpu
        x_2_o = unroll_template(state, boundary)

        if i == 0:
          with tf.device('/cpu:0'):
            if len(x_2_o.get_shape()) == 5:
              tf.summary.image('generated_d_' + str(i), x_2_o[:,0,:,:,0:1])
              tf.summary.image('generated_d_' + str(i), x_2_o[:,0,:,:,2:5])
              tf.summary.image('true_d_' + str(i), state[:,0,:,:,0:1])
              tf.summary.image('true_d_' + str(i), state[:,0,:,:,2:5])
            elif len(x_2_o.get_shape()) == 6:
              tf.summary.image('generated_d_' + str(i), x_2_o[:,0,0,:,:,2:5])
              tf.summary.image('generated_d2_' + str(i), x_2_o[:,0,0,:,:,1:2])
              tf.summary.image('true_y_' + str(i), state[:,0,0,:,:,2:5])
              tf.summary.image('true_y2_' + str(i), state[:,0,0,:,:,1:2])
   
        # if i is one then get variables to store all trainable params and 
        if i == 0:
          all_params = tf.trainable_variables()

        # loss mse
        '''
        #this block comment is his code
        error_mse = loss_mse(state, x_2_o)
        error_gradient = loss_gradient_difference(state, x_2_o)
        error = error_mse + FLAGS.lambda_divergence * error_gradient
        loss_gen.append(error)
        '''
        
        ##add in huber loss
        error = loss_huber(state, x_2_o, delta=4.5)
        loss_gen.append(error)

        # store gradients
        grads.append(tf.gradients(loss_gen[i], all_params))

    # exponential moving average for training
    ema = tf.train.ExponentialMovingAverage(decay=.9995)
    maintain_averages_op = tf.group(ema.apply(all_params))

    # store up the loss and gradients on gpu:0
    with tf.device('/gpu:0'):
      for i in range(1, FLAGS.nr_gpus):
        loss_gen[0] += loss_gen[i]
        for j in range(len(grads[0])):
          grads[0][j] += grads[i][j]

      # train (hopefuly)
      optimizer = tf.group(adam_updates(all_params, grads[0], lr=FLAGS.reconstruction_lr, mom1=0.95, mom2=0.9995), maintain_averages_op, global_step.assign_add(1))

    # set total loss for printing
    total_loss = loss_gen[0]
    tf.summary.scalar('total_loss', total_loss)
    
    # List of all Variables
    variables = tf.global_variables()

    # Build a saver
    saver = tf.train.Saver(variables, max_to_keep=1)

    # Summary op
    summary_op = tf.summary.merge_all()
 
    # build initialization
    init = tf.global_variables_initializer()

    # Start running operations on the Graph.
    sess = tf.Session()
    
    # initalize
    sess.run(init)

    # init from checkpoint dir
    saver_restore = tf.train.Saver(variables)
    ckpt = tf.train.get_checkpoint_state(TRAIN_DIR)
    if ckpt is not None:
      print("init from " + TRAIN_DIR)
      try:
         saver_restore.restore(sess, ckpt.model_checkpoint_path)
      except:
         print("No usable checkpoing found here. Now using random init")

    # Start que runner
    tf.train.start_queue_runners(sess=sess)

    # Summary op
    graph_def = sess.graph.as_graph_def(add_shapes=True)
    summary_writer = tf.summary.FileWriter(TRAIN_DIR, graph_def=graph_def)

    # start timer
    t = time.time()

    # calc number of steps left to run
    run_steps = FLAGS.max_steps - int(sess.run(global_step))
    for step in xrange(run_steps):
      current_step = int(sess.run(global_step))
      _ , loss_value = sess.run([optimizer, total_loss],feed_dict={})

      assert not np.isnan(loss_value), 'Model diverged with loss = NaN'

      if current_step%200 == 0:
        elapsed = time.time() - t
        print("loss value at " + str(loss_value))
        print("time per batch is " + str(elapsed/200.))
        t = time.time()

      if (current_step+1)%2000 == 0:
        time.sleep(10) # helps queue not get stuck (newer versions of tf should fix this)
        summary_str = sess.run(summary_op, feed_dict={})
        summary_writer.add_summary(summary_str, current_step) 
        checkpoint_path = os.path.join(TRAIN_DIR, 'model.ckpt')
        saver.save(sess, checkpoint_path, global_step=global_step)  
        print("saved to " + TRAIN_DIR)

def main(argv=None):  # pylint: disable=unused-argument
  if not tf.gfile.Exists(TRAIN_DIR):
    tf.gfile.MakeDirs(TRAIN_DIR)
  if tf.gfile.Exists(TRAIN_DIR) and not FLAGS.restore:
    tf.gfile.DeleteRecursively(TRAIN_DIR)
  train()

if __name__ == '__main__':
  tf.app.run()
