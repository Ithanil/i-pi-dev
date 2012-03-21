"""Contains the classes that deal with constant temperature dynamics.

Contains the algorithms which propagate the thermostatting steps in the constant
temperature ensembles. Includes the new GLE thermostat, which can be used to 
run PI+GLE dynamics, reducing the number of path integral beads required.

Classes:
   Thermostat: Base thermostat class with the generic methods and attributes.
   ThermoLangevin: Holds the algorithms for a langevin thermostat.
   ThermoPILE_L: Holds the algorithms for a path-integral langevin equation
      thermostat, with a thermostat coupled directly to the 
      centroid coordinate of each bead.
   ThermoPILE_G: Holds the algorithms for a path-integral langevin equation 
      thermostat, with a thermostat coupled to the kinetic energy for 
      the entire system.
   ThermoSVR: Holds the algorithms for a stochastic velocity rescaling
      thermostat.
   ThermoGLE: Holds the algorithms for a generalised langevin equation 
      thermostat.
   RestartThermo: Deals with creating the thermostat object from a file, and
      writing the checkpoints.
"""

__all__ = ['Thermostat', 'ThermoLangevin', 'ThermoPILE_L', 'ThermoPILE_G',
           'ThermoSVR', 'ThermoGLE', 'ThermoNMGLE', 'RestartThermo']

import numpy as np
import math
from utils.depend   import *
from utils.units    import *
from utils.restart  import *
from utils.mathtools import matrix_exp, stab_cholesky
from utils.prng import Random
from beads import Beads

class Thermostat(dobject): 
   """Base thermostat class.

   Gives the standard methods and attributes needed in all the thermostat
   classes.

   Attributes:
      prng: A pseudo random number generator object.
      ndof: The number of degrees of freedom the thermostat will be coupled to.

   Depend objects:
      dt: The time step used in the algorithms. Depends on the simulation dt.
      temp: The simulation temperature. Higher than the system temperature by
         a factor of the number of beads. Depends on the simulation temp.
      ethermo: The total energy exchanged with the bath due to the thermostat.
      p: The momentum vector that the thermostat is coupled to. Depends on the
         beads p object.
      m: The mass vector associated with p. Depends on the beads m object.
      sm: The square root of the mass vector.
   """

   def __init__(self, temp = 1.0, dt = 1.0, ethermo=0.0):
      """Initialises Thermostat.

      Args:
         temp: The simulation temperature. Defaults to 1.0. 
         dt: The simulation time step. Defaults to 1.0.
         ethermo: The initial heat energy transferred to the bath. 
            Defaults to 0.0. Will be non-zero if the thermostat is 
            initialised from a checkpoint file.
      """

      dset(self,"temp",   depend_value(name='temp', value=temp))
      dset(self,"dt",     depend_value(name='dt', value=dt))      
      dset(self,"ethermo",depend_value(name='ethermo',value=ethermo))

   def bind(self, beads=None, atoms=None, cell=None, pm=None, prng=None, ndof=None):
      """Binds the appropriate degrees of freedom to the thermostat.

      This takes an object with degrees of freedom, and makes their momentum
      and mass vectors members of the thermostat. It also then creates the 
      objects that will hold the data needed in the thermostat algorithms 
      and the dependency network.

      Args:
         beads: An optional beads object to take the mass and momentum vectors 
            from.
         atoms: An optional atoms object to take the mass and momentum vectors
            from.
         cell: An optional cell object to take the mass and momentum vectors 
            from.
         pm: An optional tuple containing a single momentum value and its
            conjugate mass.
         prng: An optional pseudo random number generator object. Defaults to
            Random().
         ndof: An optional integer which can specify the total number of
            degrees of freedom. Defaults to len(p). Used if conservation of
            linear momentum is being applied, as this removes three degrees of 
            freedom.

      Raises:
         TypeError: Raised if no appropriate degree of freedom or object
            containing a momentum vector is specified for 
            the thermostat to couple to.
      """

      if prng is None:
         self.prng = Random()
      else:
         self.prng = prng  
      
      if not beads is None:
         dset(self,"p",beads.p.flatten())
         dset(self,"m",beads.m3.flatten())     
      elif not atoms is None:
         dset(self,"p",dget(atoms, "p"))
         dset(self,"m",dget(atoms, "m3"))               
      elif not cell is None:   
         dset(self,"p",dget(cell, "p6"))
         dset(self,"m",dget(cell, "m6"))      
      elif not pm is None:   
         dset(self,"p",pm[0])
         dset(self,"m",pm[1])               
      else: 
         raise TypeError("Thermostat.bind expects either Beads, Atoms, a Cell, or a (p,m) tuple to bind to")
      
      if ndof is None:
         self.ndof = len(self.p)
      else:
         self.ndof = ndof
      
      dset(self,"sm",depend_array(name="sm", value=np.zeros(len(dget(self,"m"))), 
                                     func=self.get_sm, dependencies=[dget(self,"m")] ) )
      
   def get_sm(self):
      """Retrieves the square root of the mass matrix.

      Returns:
         A vector of the square root of the mass matrix with one value for
         each degree of freedom.
      """

      return np.sqrt(self.m)
   
   def step(self):                
      """Dummy thermostat step."""       
      
      pass

class ThermoLangevin(Thermostat):     
   """Represents a langevin thermostat.

   Depend objects:
      tau: Thermostat damping time scale. Larger values give a less strongly
         coupled thermostat.
      T: Coefficient of the diffusive contribution of the thermostat, i.e. the
         drift back towards equilibrium. Depends on tau and the time step.
      S: Coefficient of the stochastic contribution of the thermostat, i.e. 
         the uncorrelated Gaussian noise. Depends on T and the temperature.
   """

   def get_T(self):
      """Calculates the coefficient of the overall drift of the velocities."""

      return math.exp(-0.5*self.dt/self.tau)
      
   def get_S(self):      
      """Calculates the coefficient of the white noise."""

      return math.sqrt(Constants.kb*self.temp*(1 - self.T**2))   
  
   def __init__(self, temp = 1.0, dt = 1.0, tau = 1.0, ethermo=0.0):
      """Initialises ThermoLangevin.

      Args:
         temp: The simulation temperature. Defaults to 1.0. 
         dt: The simulation time step. Defaults to 1.0.
         tau: The thermostat damping timescale. Defaults to 1.0.
         ethermo: The initial heat energy transferred to the bath. 
            Defaults to 0.0. Will be non-zero if the thermostat is 
            initialised from a checkpoint file.
      """

      super(ThermoLangevin,self).__init__(temp, dt, ethermo)
      
      dset(self,"tau",depend_value(value=tau,name='tau'))
      dset(self,"T",  depend_value(name="T",func=self.get_T, dependencies=[dget(self,"tau"), dget(self,"dt")]))
      dset(self,"S",  depend_value(name="S",func=self.get_S, dependencies=[dget(self,"temp"), dget(self,"T")]))
      
   def step(self):
      """Updates the bound momentum vector with a langevin thermostat."""

      p = self.p.view(np.ndarray).copy()
      
      p /= self.sm

      self.ethermo += np.dot(p,p)*0.5
      p *= self.T
      p += self.S*self.prng.gvec(len(p))
      self.ethermo -= np.dot(p,p)*0.5      

      p *= self.sm      
            
      self.p = p

class ThermoPILE_L(Thermostat):    
   """Represents a PILE thermostat with a local centroid thermostat.

   Attributes:
      _thermos: The list of the different thermostats for all the ring polymer
         normal modes.

   Depend objects:
      tau: Centroid thermostat damping time scale. Larger values give a 
         less strongly coupled centroid thermostat.
      tauk: Thermostat damping time scale for the non-centroid normal modes.
         Depends on the ring polymer spring constant, and thus the simulation
         temperature.

   Raises:
      TypeError: Raised if the thermostat is used with any object other than
         a beads object, so that we make sure that the objects needed for the
         normal mode transformation exist.
   """

   def __init__(self, temp = 1.0, dt = 1.0, tau = 1.0, ethermo=0.0):
      """Initialises ThermoPILE_L.

      Args:
         temp: The simulation temperature. Defaults to 1.0. 
         dt: The simulation time step. Defaults to 1.0.
         tau: The centroid thermostat damping timescale. Defaults to 1.0.
         ethermo: The initial conserved energy quantity. Defaults to 0.0. Will
            be non-zero if the thermostat is initialised from a checkpoint file.
      """

      super(ThermoPILE_L,self).__init__(temp,dt,ethermo)
      dset(self,"tau",depend_value(value=tau,name='tau'))

   def bind(self, beads=None, prng=None, bindcentroid=True, ndof=None):  
      """Binds the appropriate degrees of freedom to the thermostat.

      This takes a beads object with degrees of freedom, and makes its momentum
      and mass vectors members of the thermostat. It also then creates the 
      objects that will hold the data needed in the thermostat algorithms 
      and the dependency network.

      Gives the interface for both the PILE_L and PILE_G thermostats, which 
      only differ in their treatment of the centroid coordinate momenta. 

      Args:
         beads: An optional beads object to take the mass and momentum vectors 
            from.
         prng: An optional pseudo random number generator object. Defaults to
            Random().
         bindcentroid: An optional boolean which decides whether a Langevin
            thermostat is attached to the centroid mode of each atom 
            separately, or the total kinetic energy. Defaults to True, which
            gives a thermostat bound to each centroid momentum.
         ndof: An optional integer which can specify the total number of
            degrees of freedom. Defaults to len(p). Used if conservation of
            linear momentum is being applied, as this removes three degrees of 
            freedom.

      Raises:
         TypeError: Raised if no appropriate degree of freedom or object
            containing a momentum vector is specified for 
            the thermostat to couple to.
      """

      if beads is None or not type(beads) is Beads:
         raise TypeError("ThermoPILE_L.bind expects a Beads argument to bind to")
      if prng is None:
         self.prng = Random()
      else:
         self.prng = prng  
      
      # creates a set of thermostats to be applied to individual normal modes
      self._thermos = [ ThermoLangevin(temp=1, dt=1, tau=1) for b in range(beads.nbeads) ]
      # optionally does not bind the centroid, so we can re-use all of this 
      # in the PILE_G case
      if not bindcentroid:
         self._thermos[0] = None
      
      dset(self,"tauk",depend_array(name="tauk", value=np.zeros(beads.nbeads-1,float), func=self.get_tauk, dependencies=[dget(self,"temp")]) )
      
      # must pipe all the dependencies in such a way that values for the nm thermostats
      # are automatically updated based on the "master" thermostat
      def make_taugetter(k):
         return lambda: self.tauk[k-1]
      it = 0
      for t in self._thermos:
         if t is None: 
            it += 1
            continue   
         if it > 0:
            ndof = None # only the centroid thermostat may have ndof!=3Nat

         t.bind(pm=(beads.pnm[it,:],beads.m3[0,:]),prng=self.prng, ndof=ndof) # bind thermostat t to the it-th bead
         # pipes temp and dt
         deppipe(self,"temp", t, "temp")
         deppipe(self,"dt", t, "dt")

         # for tau it is slightly more complex
         if it == 0:
            deppipe(self,"tau", t, "tau")
         else:
            # here we manually connect _thermos[i].tau to tauk[i]. simple and clear.
            dget(t,"tau").add_dependency(dget(self,"tauk"))
            dget(t,"tau")._func = make_taugetter(it)
         dget(self,"ethermo").add_dependency(dget(t,"ethermo"))
         it += 1     
             
      dget(self,"ethermo")._func = self.get_ethermo;
         
   def get_tauk(self):
      """Computes the thermostat damping time scale for the non-centroid 
      normal modes.

      Returns:
         An array with the damping time scales for the non-centroid modes.
      """

      return np.array([1.0/(4*self.temp*Constants.kb/Constants.hbar*math.sin(k*math.pi/len(self._thermos))) for k in range(1,len(self._thermos)) ])

   def get_ethermo(self):
      """Computes the total energy transferred to the heat bath for all the 
      thermostats.
      """

      et = 0.0;
      for t in self._thermos:
         et += t.ethermo
      return et
            
   def step(self):
      """Updates the bound momentum vector with a PILE thermostat."""

      # super-cool! just loop over the thermostats! it's as easy as that! 
      for t in self._thermos:
         t.step()        

class ThermoSVR(Thermostat):     
   """Represents a stochastic velocity rescaling thermostat.

   Depend objects:
      tau: Centroid thermostat damping time scale. Larger values give a 
         less strongly coupled centroid thermostat.
      K: Scaling factor for the total kinetic energy. Depends on the 
         temperature.
      et: Parameter determining the strength of the thermostat coupling.
         Depends on tau and the time step.
   """

   def get_et(self):
      """Calculates the thermostat coupling strength parameter."""

      return math.exp(-0.5*self.dt/self.tau)

   def get_K(self):
      """Calculates the total kinetic energy scaling factor.

      As everywhere the total kinetic energy is used in the algorithm it is
      scaled by a factor of 2*beta, this factor is calculated here for
      convenience.
      """

      return Constants.kb*self.temp*0.5
      
   def __init__(self, temp = 1.0, dt = 1.0, tau = 1.0, ethermo=0.0):
      """Initialises ThermoSVR.

      Args:
         temp: The simulation temperature. Defaults to 1.0. 
         dt: The simulation time step. Defaults to 1.0.
         tau: The thermostat damping timescale. Defaults to 1.0.
         ethermo: The initial conserved energy quantity. Defaults to 0.0. Will
            be non-zero if the thermostat is initialised from a checkpoint file.
      """

      super(ThermoSVR,self).__init__(temp,dt,ethermo)
      
      dset(self,"tau",depend_value(value=tau,name='tau'))
      dset(self,"et",  depend_value(name="et",func=self.get_et, dependencies=[dget(self,"tau"), dget(self,"dt")]))
      dset(self,"K",  depend_value(name="K",func=self.get_K, dependencies=[dget(self,"temp")]))
      
   def step(self):
      """Updates the bound momentum vector with a stochastic velocity rescaling
      thermostat.
      """
      
      K = np.dot(depstrip(self.p),depstrip(self.p)/depstrip(self.m))*0.5
      
      r1 = self.prng.g
      if (self.ndof-1)%2 == 0:
         rg = 2.0*self.prng.gamma((self.ndof-1)/2)
      else:
         rg = 2.0*self.prng.gamma((self.ndof-2)/2) + self.prng.g**2
            
      alpha2 = self.et+self.K/K*(1-self.et)*(r1**2 + rg) + 2.0*r1*math.sqrt(self.K/K*self.et*(1-self.et))
      alpha = math.sqrt(alpha2)
      if (r1 + math.sqrt(2*K/self.K*self.et/(1-self.et))) < 0:
         alpha *= -1

      self.ethermo += K*(1-alpha2)
      self.p *= alpha

class ThermoPILE_G(ThermoPILE_L):    
   """Represents a PILE thermostat with a global centroid thermostat.

   Simply replaces the Langevin thermostat for the centroid normal mode with
   a global velocity rescaling thermostat.
   """

   def __init__(self, temp = 1.0, dt = 1.0, tau = 1.0, ethermo=0.0):
      """Initialises ThermoPILE_G.

      Args:
         temp: The simulation temperature. Defaults to 1.0. 
         dt: The simulation time step. Defaults to 1.0.
         tau: The centroid thermostat damping timescale. Defaults to 1.0.
         ethermo: The initial conserved energy quantity. Defaults to 0.0. Will
            be non-zero if the thermostat is initialised from a checkpoint file.
      """

      super(ThermoPILE_G,self).__init__(temp,dt,tau,ethermo)
      
   def bind(self, beads=None, prng=None, ndof=None):   
      """Binds the appropriate degrees of freedom to the thermostat.

      This takes a beads object with degrees of freedom, and makes its momentum
      and mass vectors members of the thermostat. It also then creates the 
      objects that will hold the data needed in the thermostat algorithms 
      and the dependency network.

      Uses the PILE_L bind interface, with bindcentroid set to false so we can
      specify that thermostat separately, by binding a global 
      thermostat to the centroid mode.

      Args:
         beads: An optional beads object to take the mass and momentum vectors 
            from.
         prng: An optional pseudo random number generator object. Defaults to
            Random().
         ndof: An optional integer which can specify the total number of
            degrees of freedom. Defaults to len(p). Used if conservation of
            linear momentum is being applied, as this removes three degrees of 
            freedom.
      """

      # first binds as a local PILE, then substitutes the thermostat on the centroid
      super(ThermoPILE_G,self).bind(beads=beads,prng=prng,bindcentroid=False, ndof=ndof) 
            
      self._thermos[0] = ThermoSVR(temp=1, dt=1, tau=1) 
      
      t = self._thermos[0]      
      t.bind(pm=(beads.pnm[0,:],beads.m3[0,:]),prng=self.prng, ndof=ndof)
      deppipe(self,"temp", t, "temp")
      deppipe(self,"dt", t, "dt")
      deppipe(self,"tau", t, "tau")
      dget(self,"ethermo").add_dependency(dget(t,"ethermo"))

class ThermoGLE(Thermostat):     
   """Represents a GLE thermostat.

   This is similar to a langevin thermostat, in that it uses Gaussian random
   numbers to simulate a heat bath acting on the system, but simulates a 
   non-Markovian system by using a Markovian formulation in an extended phase
   space. This allows for a much greater degree of flexibility, and this 
   thermostat, properly fitted, can give the an approximation to the correct
   quantum ensemble even for a classical, 1-bead simulation. More reasonably, 
   using this thermostat allows for a far smaller number of replicas of the
   system to be used, as the convergence of the properties
   of the system is accelerated with respect to number of beads when PI+GLE 
   are used in combination. (See M. Ceriotti, D. E. Manolopoulos, M. Parinello,
   J. Chem. Phys. 134, 084104 (2011)).

   Attributes: 
      ns: The number of auxilliary degrees of freedom.
      s: An array holding all the momenta, including the ones for the
         auxilliary degrees of freedom.

   Depend objects:
      A: Drift matrix giving the damping time scales for all the different 
         degrees of freedom.
      C: Static covariance matrix. 
         Satisfies A.C + C.transpose(A) = B.transpose(B), where B is the 
         diffusion matrix, giving the strength of the coupling of the system
         with the heat bath, and thus the size of the stochastic 
         contribution of the thermostat.
      T: Matrix for the diffusive contribution of the thermostat, i.e. the
         drift back towards equilibrium. Depends on A and the time step.
      S: Matrix for the stochastic contribution of the thermostat, i.e. 
         the uncorrelated Gaussian noise. Depends on C and T.
   """

   def get_T(self):
      """Calculates the matrix for the overall drift of the velocities."""

      print "Getting T", self.A
      return matrix_exp(-0.5*self.dt*self.A)
      
   def get_S(self):      
      """Calculates the matrix for the coloured noise."""
      print "Getting S", self.C
      SST = Constants.kb*(self.C - np.dot(self.T,np.dot(self.C,self.T.T)))
      return stab_cholesky(SST)
  
   def get_C(self):
      """Calculates C from temp (if C is not set explicitely)"""
      rC = np.identity(self.ns + 1,float)*self.temp
      return rC[:]
      
   def __init__(self, temp = 1.0, dt = 1.0, A = None, C = None, ethermo=0.0):
      """Initialises ThermoGLE.

      Args:
         temp: The simulation temperature. Defaults to 1.0. 
         dt: The simulation time step. Defaults to 1.0.
         A: An optional matrix giving the drift matrix. Defaults to a single
            value of 1.0.
         C: An optional matrix giving the covariance matrix. Defaults to an 
            identity matrix times temperature with the same dimensions as the
            total number of degrees of freedom in the system.
         ethermo: The initial heat energy transferred to the bath. 
            Defaults to 0.0. Will be non-zero if the thermostat is 
            initialised from a checkpoint file.
      """

      super(ThermoGLE,self).__init__(temp,dt,ethermo)
      
      if A is None:
         A = np.identity(1,float)
      dset(self,"A",depend_value(value=A.copy(),name='A'))

      self.ns = len(self.A) - 1;

      # now, this is tricky. if C is taken from temp, then we want it to be updated
      # as a depend of temp. Otherwise, we want it to be an independent beast.
      if C is None: 
         C = np.identity(self.ns+1,float)*self.temp         
         dset(self,"C",depend_value(name='C', func=self.get_C, dependencies=[dget(self,"temp")]))
      else:
         dset(self,"C",depend_value(value=C.copy(),name='C'))
      
      dset(self,"T",  depend_value(name="T",func=self.get_T, dependencies=[dget(self,"A"), dget(self,"dt")]))      
      dset(self,"S",  depend_value(name="S",func=self.get_S, dependencies=[dget(self,"C"), dget(self,"T")]))
      
      self.s = np.zeros(0)
  
   def bind(self, beads=None, atoms=None, cell=None, pm=None, prng=None, ndof=None):
      """Binds the appropriate degrees of freedom to the thermostat.

      This takes an object with degrees of freedom, and makes their momentum
      and mass vectors members of the thermostat. It also then creates the 
      objects that will hold the data needed in the thermostat algorithms 
      and the dependency network.

      Args:
         beads: An optional beads object to take the mass and momentum vectors 
            from.
         atoms: An optional atoms object to take the mass and momentum vectors
            from.
         cell: An optional cell object to take the mass and momentum vectors 
            from.
         pm: An optional tuple containing a single momentum value and its
            conjugate mass.
         prng: An optional pseudo random number generator object. Defaults to
            Random().
         ndof: An optional integer which can specify the total number of
            degrees of freedom. Defaults to len(p). Used if conservation of
            linear momentum is being applied, as this removes three degrees of 
            freedom.

      Raises:
         TypeError: Raised if no appropriate degree of freedom or object
            containing a momentum vector is specified for 
            the thermostat to couple to.
      """

      super(ThermoGLE,self).bind(beads,atoms,cell,pm,prng,ndof)

      # allocates, initializes or restarts an array of s's 
      if self.s.shape != ( self.ns + 1, len(dget(self,"m") )) :
         if len(self.s) > 0:
            print " @ GLE BIND: Warning: s array size mismatch on restart! "
         self.s = np.zeros((self.ns + 1,len(dget(self,"m"))))
         
         # Initializes the s vector in the free-particle limit
         SC = stab_cholesky(self.C*Constants.kb)         
         self.s = np.dot(SC, self.prng.gvec(self.s.shape)) 
      else:
         print " @ GLE BIND: Restarting additional DOFs! "
              
   def step(self):
      """Updates the bound momentum vector with a GLE thermostat"""      
      
      p = self.p.view(np.ndarray).copy()
      
      self.s[0,:] = self.p/self.sm

      self.ethermo += np.dot(self.s[0],self.s[0])*0.5
      self.s = np.dot(self.T,self.s) + np.dot(self.S,self.prng.gvec(self.s.shape))
      self.ethermo -= np.dot(self.s[0],self.s[0])*0.5

      self.p = self.s[0]*self.sm

class ThermoNMGLE(Thermostat):     
   """Represents a "normal-modes" GLE thermostat.

   An extension to the GLE thermostat which is applied in the
   normal modes representation, and which allows to use a different
   GLE for each normal mode

   Attributes: 
      ns: The number of auxilliary degrees of freedom.
      nb: The number of beads.
      s: An array holding all the momenta, including the ones for the
         auxilliary degrees of freedom.

   Depend objects:
      A: Drift matrix giving the damping time scales for all the different 
         degrees of freedom (must contain nb terms).
      C: Static covariance matrix. 
         Satisfies A.C + C.transpose(A) = B.transpose(B), where B is the 
         diffusion matrix, giving the strength of the coupling of the system
         with the heat bath, and thus the size of the stochastic 
         contribution of the thermostat.
      T: Matrix for the diffusive contribution of the thermostat, i.e. the
         drift back towards equilibrium. Depends on A and the time step.
      S: Matrix for the stochastic contribution of the thermostat, i.e. 
         the uncorrelated Gaussian noise. Depends on C and T.
   """

#   def get_T(self):
#      """Calculates the matrix for the overall drift of the velocities."""

#      rv = np.ndarray((self.nb, self.ns+1, self.ns+1), float)
#      for b in range(0,self.nb) : rv[b]=matrix_exp(-0.5*self.dt*self.A[b])
#      return rv[:]
#      
#   def get_S(self):      
#      """Calculates the matrix for the coloured noise."""

#      rv = np.ndarray((self.nb, self.ns+1, self.ns+1), float)
#      for b in range(0,self.nb) :
#         SST = Constants.kb*(self.C - np.dot(self.T,np.dot(self.C,self.T.T)))
#         rv[b]=stab_cholesky(SST)
#      return rv[:]
  
   def get_C(self):
      """Calculates C from temp (if C is not set explicitely)"""

      rv = np.ndarray((self.nb, self.ns+1, self.ns+1), float)
      for b in range(0,self.nb) : rv[b] = np.identity(self.ns + 1,float)*self.temp
      return rv[:]
      
   def __init__(self, temp = 1.0, dt = 1.0, A = None, C = None, ethermo=0.0):
      """Initialises ThermoGLE.

      Args:
         temp: The simulation temperature. Defaults to 1.0. 
         dt: The simulation time step. Defaults to 1.0.
         A: An optional matrix giving the drift matrix. Defaults to a single
            value of 1.0.
         C: An optional matrix giving the covariance matrix. Defaults to an 
            identity matrix times temperature with the same dimensions as the
            total number of degrees of freedom in the system.
         ethermo: The initial heat energy transferred to the bath. 
            Defaults to 0.0. Will be non-zero if the thermostat is 
            initialised from a checkpoint file.
      """

      super(ThermoNMGLE,self).__init__(temp,dt,ethermo)
      
      if A is None:
         A = np.identity(1,float)
      dset(self,"A",depend_value(value=A.copy(),name='A'))

      self.nb = len(self.A)
      self.ns = len(self.A[0]) - 1;

      # now, this is tricky. if C is taken from temp, then we want it to be updated
      # as a depend of temp. Otherwise, we want it to be an independent beast.
      if C is None: 
         dset(self,"C",depend_value(name='C', func=self.get_C, dependencies=[dget(self,"temp")]))
      else:
         dset(self,"C",depend_value(value=C.copy(),name='C'))
      
#      dset(self,"T",  depend_value(name="T",func=self.get_T, dependencies=[dget(self,"A"), dget(self,"dt")]))      
#      dset(self,"S",  depend_value(name="S",func=self.get_S, dependencies=[dget(self,"C"), dget(self,"T")]))
#      
#      self.s = np.zeros(0)
  
   def bind(self, beads=None, atoms=None, cell=None, pm=None, prng=None, ndof=None):
      """Binds the appropriate degrees of freedom to the thermostat.

      This takes an object with degrees of freedom, and makes their momentum
      and mass vectors members of the thermostat. It also then creates the 
      objects that will hold the data needed in the thermostat algorithms 
      and the dependency network. Actually, this specific thermostat requires
      being called on a beads object.

      Args:
         beads: An optional beads object to take the mass and momentum vectors 
            from.
         atoms: An optional atoms object to take the mass and momentum vectors
            from.
         cell: An optional cell object to take the mass and momentum vectors 
            from.
         pm: An optional tuple containing a single momentum value and its
            conjugate mass.
         prng: An optional pseudo random number generator object. Defaults to
            Random().
         ndof: An optional integer which can specify the total number of
            degrees of freedom. Defaults to len(p). Used if conservation of
            linear momentum is being applied, as this removes three degrees of 
            freedom.

      Raises:
         TypeError: Raised if no appropriate degree of freedom or object
            containing a momentum vector is specified for 
            the thermostat to couple to.
      """

      if beads is None or not type(beads) is Beads:
         raise TypeError("ThermoNMGLE.bind expects a Beads argument to bind to")

      if prng is None:
         self.prng = Random()
      else:
         self.prng = prng  
      
      if (beads.nbeads != self.nb):
         raise IndexError("Number of beads "+str(beads.nbeads)+" doesn't match GLE parameters nb= "+str(self.nb) )

      # creates a set of thermostats to be applied to individual normal modes
      self._thermos = [ ThermoGLE(temp=1, dt=1, A=self.A[b], C=self.C[b]) for b in range(beads.nbeads) ]
            
      # must pipe all the dependencies in such a way that values for the nm thermostats
      # are automatically updated based on the "master" thermostat
      def make_Agetter(k):
         return lambda: self.A[k]
      def make_Cgetter(k):
         return lambda: self.C[k]

      it = 0
      for t in self._thermos:
         t.bind(pm=(beads.pnm[it,:],beads.m3[0,:]), prng=self.prng) # bind thermostat t to the it-th normal mode

         # pipes temp and dt
         deppipe(self,"temp", t, "temp")
         deppipe(self,"dt", t, "dt")

         # here we pipe the A and C of individual NM to the "master" arrays
         dget(t,"A").add_dependency(dget(self,"A"))
         dget(t,"A")._func = make_Agetter(it)
         dget(t,"C").add_dependency(dget(self,"C"))
         dget(t,"C")._func = make_Cgetter(it)
         dget(self,"ethermo").add_dependency(dget(t,"ethermo"))
         it += 1
             
      dget(self,"ethermo")._func = self.get_ethermo;

#      super(ThermoNMGLE,self).bind(beads,atoms,cell,pm,prng,ndof)

      # allocates, initializes or restarts an array of s's 
      if self.s.shape != ( self.nb, self.ns + 1, beads.natoms *3 ) :
         if len(self.s) > 0:
            print " @ GLE BIND: Warning: s array size mismatch on restart! "
         self.s = np.zeros(  ( self.nb, self.ns + 1, beads.natoms*3 )  )
         
         # Initializes the s vector in the free-particle limit
         for b in range(self.nb):
            SC = stab_cholesky(self.C[b]*Constants.kb)         
            self.s[b] = np.dot(SC, self.prng.gvec(self.s[b].shape)) 
            self._thermos[b].s=self.s[b]
      else:
         print " @ GLE BIND: Restarting additional DOFs! "
              
   def step(self):
      """Updates the thermostat in NM representation by looping over the individual DOFs."""

      for t in self._thermos:
         t.step()        

   def get_ethermo(self):
      """Computes the total energy transferred to the heat bath for all the nm thermostats. """

      et = 0.0;
      for t in self._thermos:
         et += t.ethermo
      return et

            
class RestartThermo(Restart):
   """Thermostat restart class.

   Handles generating the appropriate thermostat class from the xml input file,
   and generating the xml checkpoiunt tags and data from an instance of the
   object.

   Attributes:
      kind: An optional string giving the type of the thermostat used. Defaults
         to 'langevin'.
      ethermo: An optional float giving the amount of heat energy transferred
         to the bath. Defaults to 0.0.
      tau: An optional float giving the damping time scale. Defaults to 1.0.
      A: An optional array of floats giving the drift matrix. Defaults to 0.0.
      C: An optional array of floats giving the static covariance matrix. 
         Defaults to 0.0.
   """

   attribs = { "kind": (RestartValue, (str, "langevin")) }
   fields = { "ethermo" : (RestartValue, (float, 0.0)), 
            "tau" : (RestartValue, (float, 1.0)) ,
            "A" : (RestartArray,(float, np.zeros(0))),
            "C" : (RestartArray,(float, np.zeros(0))),
            "s" : (RestartArray,(float, np.zeros(0)))
             }
   
   def store(self, thermo):
      """Takes a thermostat instance and stores a minimal representation of it.

      Args:
         thermo: A thermostat object.
      """

      if type(thermo) is ThermoLangevin: 
         self.kind.store("langevin")
         self.tau.store(thermo.tau)
      elif type(thermo) is ThermoSVR: 
         self.kind.store("svr")
         self.tau.store(thermo.tau)         
      elif type(thermo) is ThermoPILE_L: 
         self.kind.store("pile_l")
         self.tau.store(thermo.tau)
      elif type(thermo) is ThermoPILE_G: 
         self.kind.store("pile_g")
         self.tau.store(thermo.tau)     
      elif type(thermo) is ThermoGLE: 
         self.kind.store("gle")
         self.A.store(thermo.A)
         if dget(thermo,"C")._func is None:
            self.C.store(thermo.C)
         self.s.store(thermo.s)
      else:
         self.kind.store("unknown")      
      self.ethermo.store(thermo.ethermo)
      
   def fetch(self):
      """Creates a thermostat object.

      Returns:
         A thermostat object of the appropriate type and with the appropriate
         parameters given the attributes of the RestartThermo object.
      """

      if self.kind.fetch() == "langevin":
         thermo = ThermoLangevin(tau=self.tau.fetch())
      elif self.kind.fetch() == "svr":
         thermo = ThermoSVR(tau=self.tau.fetch())
      elif self.kind.fetch() == "pile_l":
         thermo = ThermoPILE_L(tau=self.tau.fetch())
      elif self.kind.fetch() == "pile_g":
         thermo = ThermoPILE_G(tau=self.tau.fetch())
      elif self.kind.fetch() == "gle":
         rC = self.C.fetch()
         if len(rC) == 0:  rC = None
         thermo = ThermoGLE(A=self.A.fetch(),C=rC)
         thermo.s = self.s.fetch()
      elif self.kind.fetch() == "nm_gle":
         rC = self.C.fetch()
         if len(rC) == 0:   rC = None
         thermo = ThermoNMGLE(A=self.A.fetch(),C=rC)
         thermo.s = self.s.fetch()
      else:
         raise TypeError("Invalid thermostat kind " + self.kind.fetch())
         
      thermo.ethermo=self.ethermo.fetch()
      return thermo
