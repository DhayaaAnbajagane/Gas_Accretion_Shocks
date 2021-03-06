'''
Code that extracts The300 y-profiles.
Written to run on midway2.
'''

# Need this for importing healpy
import sys
sys.path.append('/project2/chihway/virtualenvs/midway2_python3/lib/python3.7/site-packages')

# Import all packages as needed
import astropy.io.fits as pf
from astropy import units as u
from astropy.table import Table
from astropy.coordinates import SkyCoord
from astropy.cosmology import Planck15 as cosmo

import matplotlib.pyplot as plt
import healpy as hp, pandas as pd, numpy as np
import colossus
from scipy import signal, interpolate, stats
from tqdm import tqdm


# Setup plotting environment
# i.e. make the ticks and ticklabels large!
import matplotlib as mpl
mpl.rcParams['xtick.direction'],   mpl.rcParams['ytick.direction']   = 'in', 'in'
mpl.rcParams['xtick.major.size'],  mpl.rcParams['xtick.minor.size']  = 14, 8
mpl.rcParams['xtick.major.width'], mpl.rcParams['xtick.minor.width'] = 1.2, 0.8
mpl.rcParams['xtick.major.pad'],   mpl.rcParams['xtick.minor.pad']   = 10, 10
mpl.rcParams['ytick.major.size'],  mpl.rcParams['ytick.minor.size']  = 14, 8
mpl.rcParams['ytick.major.width'], mpl.rcParams['ytick.minor.width'] = 1.2, 0.8

plt.rc('xtick',labelsize=22)
plt.rc('ytick',labelsize=22)

#Necessary cosmological params
h = 0.6777

'''
STEP 1: Setup
'''
#Open one cluster file just to get higher level params (redshift, resolution)
cluster = pf.open('/project2/chihway/dhayaa/The300_maps/z0p2/snap_120-TT-z-cl-1-WV.fits')

# Need redshift and pixel size (in kpc) to get pixel size in angles
# All clusters are at the same redshift, so we only need to read the
# redshift value once.

z       = cluster[0].header['REDSHIFT']
pix_ang = cluster[0].header['PSIZE'] / (cosmo.angular_diameter_distance(z).value * 1e3) / np.pi * 180

# create a phi/theta grid
image_size = int(cluster[0].header['NAXIS1'])
x = np.linspace(-pix_ang*image_size/2, pix_ang*image_size/2, image_size, endpoint = True)
y = np.linspace(-pix_ang*image_size/2, pix_ang*image_size/2, image_size, endpoint = True)
xv, yv = np.meshgrid(x, y)

#Convert phi, theta  to  Ra, dec
xv = xv + cluster[0].header['CRVAL1']
yv = yv + cluster[0].header['CRVAL2']
xv = xv * np.cos(yv * np.pi/180)

#Create flattened versions of array for convenience
x_array, y_array = xv.flatten(), yv.flatten()

#Create a SkyCoord instance of the grid to use
#for every cluster. So we'll use this same grid
#but change the SZ values on the grid according to
#each cluster.
image_coords = SkyCoord(x_array, y_array, unit = 'degree')


# Now we read in the data from The300 catalogs
# (R200m, cluster positions etc.)
Halo_data = pd.DataFrame()

#Loop over each cluster in The300
for i in range(1, 324):

    #Read in file that has all quantities, over all snapshots, for a SINGLE cluster, i
    name = '0'*(4 - len(str(i))) + str(i)
    Relaxation_Params = pd.read_csv('/project2/chihway/dhayaa/The300_maps/The300_relaxation_params/GadgetX_ALL_NewMDCLUSTER_' + name + '.dat',
                                    delimiter = ' ', skiprows = 0, skipinitialspace = True)

    #Select just the row corresponding to snapshot == 120
    Relaxation_Params = Relaxation_Params[Relaxation_Params.snapnum.values == 120]

    #The .loc function indexes into (row, column) of a dataframe
    #Here, it doesn't matter if we use .loc[i, name]
    #or .loc[i - 1, name] since we convert the columns to
    #numpy arrays later anyway.
    Halo_data.loc[i, 'M200c']    = Relaxation_Params.M200c.values[0] #Don't really need this but loading anyway
    Halo_data.loc[i, 'R200c']    = Relaxation_Params.R200c.values[0]/(1 + 0.193)/0.6777*1e-3 #Convert from comoving kpc/h to physical Mpc
    Halo_data.loc[i, 'fsubmass'] = Relaxation_Params.fsubmass.values[0]
    Halo_data.loc[i, 'Xc']       = Relaxation_Params.Xc.values[0] * 1e-3 #Convert from comoving kpc/h to comoving Mpc/h
    Halo_data.loc[i, 'Yc']       = Relaxation_Params.Yc.values[0] * 1e-3 #Convert from comoving kpc/h to comoving Mpc/h

    # Pull R200m values from Eric's file (in units physical(?) Mpc/h)
    # Need to use i - 1 as index here since i starts from 1 in our loop
    Halo_data.loc[i, 'R200m']    = np.load('R200m.npy')[i - 1] / 0.6777 #convert from Mpc/h to Mpc

#Set logarithmic bins of R/R200m for the y-profile computation
bins = np.geomspace(0.4, 4, 80)

n_clusters = 100 #Number of cluster to look at. Anywhere between 1 to 323

#Create 2D array to hold profiles for each cluster
y_vals = np.empty([n_clusters, bins.size - 1])

'''
STEP 2: Compute profile
'''
# We start index at 1, instead of 0, because The300 cluster
# naming starts from 1. So we'll use i - 1 to index
# into numpy arrays
for i in tqdm(range(1, n_clusters + 1)):

    # Check a "relaxation" condition
    # If cluster isn't relaxed, then skip.
    if Halo_data.fsubmass.values[i - 1] > 0.1: continue

    cluster = pf.open('/project2/chihway/dhayaa/The300_maps/z0p2/snap_120-TT-z-cl-' + str(i) + '-WV.fits')

    # Get a 2D SZ data from the map and flatten.
    # This array has the same size as
    # x_array and y_array
    Sim_SZ_map = cluster[0].data.flatten()

    # Find the center of the cluster in the image
    # using Halo catalog properties. Xc and Yc are
    # the centers of the cluster in comoving kpc/h.
    # 500 comoving Mpc/h is the center of the image.
    Xc_ang = (Halo_data.Xc.values[i - 1] - 500)/h/(1 + z) / cosmo.angular_diameter_distance(z).value + cluster[0].header['CRVAL1']
    Yc_ang = (Halo_data.Yc.values[i - 1] - 500)/h/(1 + z) / cosmo.angular_diameter_distance(z).value + cluster[0].header['CRVAL2']

    # Need to scale RA cos(dec) here as well
    center = (Xc_ang * np.cos(Yc_ang * np.pi / 180), Yc_ang)

    c = SkyCoord(center[0], center[1], unit = 'degree')

    # Get the separation in radians and convert to physical Mpc
    r   = c.separation(image_coords).rad * cosmo.angular_diameter_distance(z).value

    # Convert r -> separation in units of R200m
    sep = r / Halo_data.R200m.values[i - 1]

    # Extract the profiles. Following functions bins data according
    # to sep and bins, and takes the mean Sim_SZ_map value in each
    # bin. Can also use median if you wish.
    #
    # Dhayaa: Using mean vs. median makes a big difference in the profile for me
    #         Is this an issue to be thinking about?
    y_vals[i - 1, :] = stats.binned_statistic(sep, Sim_SZ_map, 'mean', bins)[0]

# Apply mask so we "drop" all the unrelaxed clusters
# from the output array. These are empty anyway.
Mask   = Halo_data.fsubmass.values[:n_clusters] < 0.1
y_vals = y_vals[Mask, :]

print("Done. Used", Mask.sum(), "relaxed halos out of", Mask.size, "halos")

# Take geometric mean of the bins
# Will use this for plotting
bins_plot = np.sqrt(bins[:-1]*bins[1:])

'''
STEP 3: Jacknife Cov
'''
# Generated index between [0, number of relaxed clusters].
# Need this for the jacknife estimation below
cluster_indices = np.arange(y_vals.shape[0], dtype = int)
stacked_y_vals  = np.empty([y_vals.shape[0], bins.size - 1])

#Loop over each "row" of the dataset
for i in range(y_vals.shape[0]):

    # The "np.delete(cluster_indices, i)" command lets us index into
    # All clusters but cluster "i". The use of nanmedian is not necessary.
    # Can just use np.median, or even np.mean
    stacked_y_vals[i, :] = np.nanmedian(y_vals[np.delete(cluster_indices, i), :],
                                                 axis = 0)

'''
STEP 4: Smoothing
'''

plt.figure(figsize=(12,8))
plt.grid()
plt.yscale('log')
plt.xscale('log')

window_l = 13

# First get the median stacked_y_vals profile.
# Perform the SG filter on the log stacked_y_vals, and
# exponentiate to get the linear stacked_y_vals again
# Use of nanpercentile is not necessary for the sims righ now.
mean_y_vals = np.e**signal.savgol_filter(np.log(np.nanpercentile(stacked_y_vals, 50, axis = 0)), window_l, 2, mode='nearest')

plt.plot(bins_plot, mean_y_vals, lw = 4, color = 'C4', label = 'Our result')

# Do the same as the above, but for the 68% bounds of
# the profiles.
plt.fill_between(bins_plot,
                 np.e**signal.savgol_filter(np.log(np.nanpercentile(stacked_y_vals, 84, axis = 0)), window_l, 2, mode='nearest'),
                 np.e**signal.savgol_filter(np.log(np.nanpercentile(stacked_y_vals, 16, axis = 0)), window_l, 2, mode='nearest'),
                 alpha = 0.4, color = 'C4')

#Load result from Eric's plot and plot
Eric_data = pd.read_csv('/project2/chihway/dhayaa/The300_maps/Eric_Profile.csv', skiprows = 1)
plt.plot(Eric_data.X.values, Eric_data.Y.values, c = 'C0', lw = 4, alpha = 0.5, label = "Eric's result")

plt.xlabel('R/R200m', size = 30)
plt.ylabel('<y>', size = 35)
plt.title('All', size = 25)
plt.legend(fontsize = 25)
plt.ylim(top = 1e-5, bottom = 3e-8)
plt.show()
