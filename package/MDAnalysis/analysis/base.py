# -*- Mode: python; tab-width: 4; indent-tabs-mode:nil; coding:utf-8 -*-
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
#
# MDAnalysis --- http://www.MDAnalysis.org
# Copyright (c) 2006-2015 Naveen Michaud-Agrawal, Elizabeth J. Denning, Oliver Beckstein
# and contributors (see AUTHORS for the full list)
#
# Released under the GNU Public Licence, v2 or any higher version
#
# Please cite your use of MDAnalysis in published work:
#
# N. Michaud-Agrawal, E. J. Denning, T. B. Woolf, and O. Beckstein.
# MDAnalysis: A Toolkit for the Analysis of Molecular Dynamics Simulations.
# J. Comput. Chem. 32 (2011), 2319--2327, doi:10.1002/jcc.21787
#

"""
Analysis building blocks --- :mod:`MDAnalysis.analysis.base`
============================================================
A collection of useful building blocks for creating Analysis
classes.
"""

import logging
import multiprocessing as mp
import copy
from operator import itemgetter
import MDAnalysis as mda


logger = logging.getLogger(__name__)


class AnalysisBase(object):
    """Base class for defining multi frame analysis
    The analysis base class is designed as a template for creating
    multiframe analysis.
    The class implements the following methods:
    _setup_frames(trajectory, start=None, stop=None, step=None)
      Pass a Reader object and define the desired iteration pattern
      through the trajectory
    run
      The user facing run method.  Calls the analysis methods
      defined below
    Your analysis can implement the following methods, which are
    called from run:
    _prepare
      Called before iteration on the trajectory has begun.
      Data structures can be set up at this time, however most
      error checking should be done in the __init__
    _single_frame
      Called after the trajectory is moved onto each new frame.
    _conclude
      Called once iteration on the trajectory is finished.
      Apply normalisation and averaging to results here.
    """
    def _setup_frames(self, universe, start=None, stop=None, step=None):
        """
        Add method docstring
        """
        if universe is None:
            pass
        else:
            self._universe = universe
            self._trajectory = self._universe.trajectory

            start, stop, step = self._trajectory.check_slice_indices(start,
                                                                     stop,
                                                                     step)
            self.start = start
            self.stop = stop
            self.step = step
            self.nframes = len(xrange(start, stop, step))

    def _single_frame(self, timestep):
        """Calculate data from a single frame of trajectory
        Don't worry about normalising, just deal with a single frame.
        """
        pass

    def _prepare(self):
        """Set things up before the analysis loop begins"""
        pass

    def _conclude(self):
        """Finalise the results you've gathered.
        Called at the end of the run() method to finish everything up.
        """
        pass

    def run(self, parallel=None, threads=None):
        """ Chooses whether to run analysis in serial or parallel
        mode depending on user input"""
        if parallel is None:
            self._serial_run()
        else:
            self._parallel_run(threads)

    def _serial_run(self):
        """Perform the calculation"""
        logger.info("Starting preparation")
        prog = mda.lib.log.Progressbar([self.nframes])
        prog.start()
        self._prepare()
        for i, ts in enumerate(
                self._trajectory[self.start:self.stop:self.step]):
            #logger.info("--> Doing frame {} of {}".format(i+1, self.nframes))
            self._single_frame(ts)
            prog.update(0)
        #logger.info("Finishing up")
        self._conclude()

    def _parallel_run(self, threads=None):
        """
        Create copies of the original object to be
        dispatched to multiprocess
        """

        if threads is None:
            self.threads = mp.cpu_count()-1
        elif threads > mp.cpu_count():
            self.threads = mp.cpu_count()-1
        else:
            self.threads = threads

        self.slices = self._compute_slices()
        self.topology = self._universe.filename
        self.trajname = self._universe._trajectory.filename
        self.nframes = 0

        threads = self.threads

        # Queues for the communication between parent and child processes
        out_queue = mp.Manager().Queue()
        progress = mp.Manager().Queue()

        # Prepare multiprocess objects
        processes = [mp.Process(target=self._compute,
                                args=(out_queue, order, progress))
                     for order in range(threads)]

        # Run processes
        for process in processes:
            process.start()

        thread_configs = [1+(elem[1]-elem[0]-1)/self.step
                          for elem in self.slices]

        name = self._type()

        prog = mda.lib.log.Progressbar(thread_configs, bar_length=50, name=name)
        prog.start()

        while any(process.is_alive() for process in processes):
            while not progress.empty():
                core = progress.get()
                prog.update(core)

        # Exit the completed processes
        for process in processes:
            process.join()

        results = []

        # Collects results from the queue
        while not out_queue.empty():
            results.append(out_queue.get())

        for thread in sorted(results, key=itemgetter(1)):
            self += thread[0]

        self._conclude()

    def _compute_slices(self):
        """
        This function returns a list containing the start and
         last configuration to be analyzed from each thread
        """
        threads = self.threads # Get number of threads from initialization
        step = self.step
        configs = 1+(self.stop-self.start-1)/step

        #self.nframes = configs

        # Check whether the number of threads is higher than
        # the number of trajectories to be analyzed
        while configs/threads == 0:
            threads -= 1

        self.threads = threads # Update the number of threads

        print "Total configurations: "+str(configs)
        print "Analysis running on ", threads, " threads.\n"

        # Number of cfgs for each thread, and remainder to be added
        thread_cfg = configs/threads
        reminder = configs%threads

        slices = []
        beg = self.start

        # Compute the start and last configurations
        for thread in range(0, threads):
            if thread < reminder:
                end = beg+step*thread_cfg
            else:
                end = beg+step*(thread_cfg-1)

            slices.append([beg, end+1])
            beg = end+step

        # Print on screen the configurations assigned to each thread
        for thread in range(threads):
            confs = 1+(slices[thread][1]-1-slices[thread][0])/step
            digits = len(str(self.stop))
            line = "Thread "+str(thread+1).rjust(len(str(threads)))+": " \
                          +str(slices[thread][0]).rjust(digits)+"/"  \
                          +str(slices[thread][1]-1).rjust(digits)    \
                          +" | Configurations: "\
                          +str(confs).rjust(1+len(str(thread_cfg)))
            print line

        return slices

    def _compute(self, out_queue, order, progress):
        """
        Run the single_frame method for each analysis object for all
        the trajectories in the batch
        """
        start = self.slices[order][0]
        stop = self.slices[order][1]
        step = self.step

        analysis_object = copy.deepcopy(self)

        analysis_object.nframes = len(xrange(start, stop, step))
        traj = analysis_object._universe.trajectory

        analysis_object._prepare()

        progress.put(order)
        for timestep in traj[start:stop:step]:
            analysis_object._single_frame(timestep)
            progress.put(order) # Updates the progress bar

        # Avoid initializing universe again when results are sent back
        analysis_object._universe.filename = None
        analysis_object._universe.trajectory.filename = None

        out_queue.put((analysis_object, order)) # Returns the results

    def _type(self):
        return self.__class__.__name__

    def __getstate__(self):
        state = dict(self.__dict__)
        # Replace the _ags entry with indices
        # pop removes the _ag key, or returns [] (empty list) if the Key didn't exist
        state['ag indices'] = [ag.indices for ag in state.pop('_ag', [])]
        try:
            state['universe filenames'] = self._universe.filename, self._universe.trajectory.filename
        except:
            pass
        state.pop('_universe', None)
        state.pop('_trajectory', None)

        return state


    def __setstate__(self, state):
        self.__dict__ = state
        # Create my local Universe
        try:
            # Create my local Universe
            self._universe = mda.Universe(*state['universe filenames'])
            # Create my local AGs
            self._ag = [self._universe.atoms[idx] for idx in state['ag indices']]
        except:
            pass

