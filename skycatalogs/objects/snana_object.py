import bisect
import galsim
import h5py
import numpy as np
from .base_object import BaseObject, ObjectCollection
from skycatalogs.utils.exceptions import SkyCatalogsRuntimeError

__all__ = ['SnanaObject', 'SnanaCollection']


class SnanaObject(BaseObject):
    _type_name = 'snana'

    def __init__(self, ra, dec, id, object_type, belongs_to, belongs_index,
                 redshift=None):
        super().__init__(ra, dec, id, object_type, belongs_to, belongs_index,
                         redshift)
        self._mjds = None
        self._lambda = None

        # indices of elements in mjd array bounding our mjd
        self._mjd_ix_l = None
        self._mjd_ix_u = None

    def _get_sed(self, mjd=None):
        if mjd is None:
            mjd = self._belongs_to._mjd
        if mjd is None:
            txt = 'SnananObject._get_sed: no mjd specified for this call\n'
            txt += 'nor when generating object list'
            raise ValueError(txt)
        mjd_start = self.get_native_attribute('start_mjd')
        mjd_end = self.get_native_attribute('end_mjd')
        if mjd < mjd_start or mjd > mjd_end:
            return None, 0.0

        return self._linear_interp_SED(mjd), 0.0

    def get_gsobject_components(self, gsparams=None, rng=None):
        if gsparams is not None:
            gsparams = galsim.GSParams(**gsparams)
        return {'this_object': galsim.DeltaFunction(gsparams=gsparams)}

    def get_observer_sed_component(self, component, mjd=None):
        sed, _ = self._get_sed(mjd=mjd)
        if sed is not None:
            sed = self._apply_component_extinction(sed)
        return sed

    def get_LSST_flux(self, band, sed=None, cache=False, mjd=None):
        # There is usually no reason to cache flux for SNe, in fact it could
        # cause problems
        def _flux_ratio(mag):
            # -0.9210340371976184 = -np.log(10)/2.5.
            return np.exp(-0.921034037196184 * mag)

        flux = super().get_LSST_flux(band, sed=sed, cache=cache, mjd=mjd)

        if flux < 0:
            raise SkyCatalogsRuntimeError('Negative flux')

        if flux == 0.0:
            return flux

        mjd_ix_l, mjd_ix_u, mjd_fraction = self._find_mjd_interval(mjd)

        with h5py.File(self._belongs_to._SED_file, 'r') as f:
            try:
                cors = f[self._id][f'magcor_{band}']
            except KeyError:
                # nothing else to do
                return flux

            # interpolate corrections
            if mjd_ix_l == mjd_ix_u:
                mag_cor = cors[mjd_ix_l]
            else:
                mag_cor = cors[mjd_ix_l] + mjd_fraction *\
                    (cors[mjd_ix_u] - cors[mjd_ix_l])

        # dbg = True
        dbg = False

        # Do everything in flux units
        flux_cor = _flux_ratio(mag_cor)
        corrected_flux = flux * flux_cor

        if dbg:
            print(f'Band {band} uncorrected flux: {flux}')
            print(f'                mag correction: {mag_cor}')
            print(f' multiplicative flux correction: {flux_cor}')

        return corrected_flux

    def _find_mjd_interval(self, mjd=None):
        '''
        Find indices into mjd array of elements bounding our mjd
        Also compute and constant needed for interpolation.  If we're
        using "standard" mjd, also store these numbers.

        Parameters
        ----------
        mjd     float   If None use the one stored in our ObjectCollection

        Returns
        -------
        A tuple:     index below, index above, and fraction of distance
                     mjd is from entry below to entry above
        '''
        if not mjd:
            mjd = self._belongs_to._mjd
            store = True
        else:
            store = (mjd == self._belongs_to._mjd)

        if store:
            if self._mjd_ix_l is not None:
                # just return previously-computed values
                return self._mjd_ix_l, self._mjd_ix_u, self._mjd_fraction

        if self._mjds is None:
            with h5py.File(self._belongs_to._SED_file, 'r') as f:
                self._mjds = np.array(f[self._id]['mjd'])
        mjds = self._mjds

        mjd_fraction = None
        index = bisect.bisect(mjds, mjd)
        if index == 0:
            mjd_ix_l = mjd_ix_u = 0
        elif index == len(mjds):
            mjd_ix_l = mjd_ix_u = index - 1
        else:
            mjd_ix_l = index - 1
            mjd_ix_u = index
            mjd_fraction = (mjd - mjds[mjd_ix_l]) /\
                (mjds[mjd_ix_u] - mjds[mjd_ix_l])
        if store:
            self._mjd_ix_l = mjd_ix_l
            self._mjd_ix_u = mjd_ix_u
            self._mjd_fraction = mjd_fraction

        return mjd_ix_l, mjd_ix_u, mjd_fraction

    def _linear_interp_SED(self, mjd=None):
        '''
        Return galsim SED obtained by interpolating between SEDs
        for nearest mjds among the templates
        '''
        mjd_ix_l, mjd_ix_u, mjd_fraction = self._find_mjd_interval(mjd)

        with h5py.File(self._belongs_to._SED_file, 'r') as f:
            if self._mjds is None or self._lambda is None:
                self._mjds = np.array(f[self._id]['mjd'])
                self._lambda = np.array(f[self._id]['lambda'])

            if mjd_ix_l == mjd_ix_u:
                flambda = f[self._id]['flamba'][mjd_ix_l]
            else:
                mjd_ix = mjd_ix_u
                below = f[self._id]['flambda'][mjd_ix - 1]
                above = f[self._id]['flambda'][mjd_ix]
                flambda = below + mjd_fraction * (above - below)

            lut = galsim.LookupTable(f[self._id]['lambda'],
                                     flambda,
                                     interpolant='linear')
        return galsim.SED(lut, wave_type='A', flux_type='flambda')


class SnanaCollection(ObjectCollection):
    '''
    This class (so far) differs from the vanilla ObjectCollection only
    in that it keeps track of where the file is which contains a library
    of SEDs for each sn
    '''
    def set_SED_file(self, SED_file):
        self._SED_file = SED_file
